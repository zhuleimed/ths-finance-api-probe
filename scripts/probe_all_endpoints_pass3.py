#!/usr/bin/env python3
"""全端点普查 · 第三遍：用官方要求的字符串日期格式 + 真实合约代码补测。

第二遍的教训：
  - special-data / fund 的日期参数要 `yyyy-MM-dd` **字符串**（不是毫秒戳）
  - prices/historical 的 start/end 反而是**毫秒戳**（两套日期规范并存）
  - 期货/期权 Contract 级端点必须给**真实合约代码**（从 contracts/list 取）

用法：
  HITHINK_FINANCE_API_KEY=<key> python scripts/probe_all_endpoints_pass3.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_all_endpoints import req, KEY, OUT, PROGRESS, SUMMARY, INTERVAL  # noqa

P3 = {
    # ── A 股热榜（yyyy-MM-dd 字符串）★与 004 相关 ──
    "/api/a-share/special-data/hot-stock-list-history": {"date": "2025-09-30"},
    "/api/a-share/special-data/hot-stock-rank-trend": {
        "thscode": "600519.SH", "start_date": "2025-09-01", "end_date": "2025-09-30"},
    # ── 基金持仓/指标（yyyy-MM-dd 字符串） ──
    "/api/fund/portfolio/stock-history": {
        "thscode": "510300.SH", "report_type": "quarterly", "end_date": "2025-09-30"},
    "/api/fund/portfolio/bond-history": {
        "thscode": "510300.SH", "report_type": "quarterly", "end_date": "2025-09-30"},
    "/api/fund/indicators/line": {
        "indexes": json.dumps([{"index_id": "nav"}]), "time_range": "1y"},
    "/api/fund/quota/list": {"tab": "QDII"},
    "/api/fund/quota/summary": {"tab": "QDII"},
    "/api/fund/offerings/list": {"subscribe": "active"},
    "/api/fund/managers/performance": {"manager_id": "30000000", "range": "1Y"},
    # ── 期货：真实合约（豆一连续/主力）──
    "/api/futures/contracts/detail": {"thscode": "A2611.DCE"},
    "/api/futures/prices/daily": {"thscode": "A2611.DCE", "start": "2025-09-01", "end": "2025-09-30"},
    "/api/futures/prices/intraday": {"thscode": "A2611.DCE"},
    "/api/futures/positions/contract-daily": {"thscode": "A2611.DCE", "variety": "A", "date": "2025-09-30"},
    "/api/futures/positions/contract-historical": {
        "thscode": "A2611.DCE", "variety": "A", "company": "中信期货", "start_date": "2025-09-01"},
    "/api/futures/warehouse-receipts/historical": {
        "thscode": "A2611.DCE", "start_date": "2025-09-01", "end_date": "2025-09-30"},
    "/api/futures/basis/historical": {"thscode": "A2611.DCE"},
    "/api/futures/calendar/session-timeline": {"thscode": "A2611.DCE"},
    "/api/futures/calendar/trading-schedule": {
        "thscode": "A2611.DCE", "start_date": "2025-09-01", "end_date": "2025-09-30"},
    # ── 期权：真实合约 ──
    "/api/options/contracts/detail": {"thscode": "10011425.SH"},
    "/api/options/prices/daily": {"thscode": "10011425.SH"},
    "/api/options/prices/intraday": {"thscode": "10011425.SH"},
    "/api/options/calendar/session-timeline": {"thscode": "10011425.SH"},
}


def main():
    if len(KEY) < 8:
        sys.exit("❌ 缺 HITHINK_FINANCE_API_KEY 环境变量")
    done = json.load(open(PROGRESS)) if os.path.exists(PROGRESS) else {}
    print(f"第三遍补测 {len(P3)} 个端点\n" + "=" * 78)
    for i, (path, params) in enumerate(P3.items(), 1):
        print(f"[{i:>2}/{len(P3)}] {path}")
        j, http, dt = req(path, params)
        code = j.get("code") if isinstance(j, dict) else "?"
        msg = str(j.get("message", ""))[:60] if isinstance(j, dict) else ""
        rec = {"http": http, "code": code, "message": msg,
               "latency_ms": round(dt), "params": params}
        if code == 0:
            d = j.get("data", {})
            if isinstance(d, dict):
                item = d.get("item")
                if isinstance(item, list):
                    rec["n_items"] = len(item)
                    if item and isinstance(item[0], dict):
                        rec["sample_fields"] = sorted(item[0].keys())
                rec["data_keys"] = sorted(d.keys())[:20]
            rec["status"] = "✅可用"
            safe = path.strip("/").replace("/", "__")
            json.dump({"path": path, "params": params, "data": d},
                      open(os.path.join(OUT, f"{safe}.json"), "w"),
                      ensure_ascii=False, indent=1, default=str)
        elif code == 2004:
            rec["status"] = "⛔客户端专用"
        elif code == 1001:
            rec["status"] = "⚠️缺参数"
        elif code == 1002:
            rec["status"] = "⚠️参数格式"
        elif code == 1003:
            rec["status"] = "⚠️取值越界"
        elif code == 3001:
            rec["status"] = "⚠️标的不存在"
        elif code == 3004:
            rec["status"] = "⚠️类型不支持"
        elif code == 5003:
            rec["status"] = "❓数据源不可用"
        else:
            rec["status"] = f"❓{code}"
        print(f"      {rec['status']}  HTTP={http} code={code} {round(dt):>4}ms "
              f"items={rec.get('n_items','-')}  {msg}")
        done[path] = rec
        json.dump(done, open(PROGRESS, "w"), ensure_ascii=False, indent=1)
        time.sleep(INTERVAL)

    summary = {}
    for p, r in done.items():
        summary.setdefault(r["status"], []).append(p)
    json.dump(summary, open(SUMMARY, "w"), ensure_ascii=False, indent=1)
    print("\n" + "=" * 78)
    print(f"最终 · 全 {len(done)} 个端点：")
    for st in sorted(summary):
        print(f"  {st:<16} {len(summary[st]):>3} 个")


if __name__ == "__main__":
    main()

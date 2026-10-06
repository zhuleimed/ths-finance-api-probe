#!/usr/bin/env python3
"""全端点普查 · 第二遍：修正参数后补测第一遍「缺参数/参数错误」的 28 个端点。

修正要点（对照官方文档）：
  - start/end/date 类参数一律要 **毫秒 Unix 时间戳**（第一遍误传 yyyyMMdd）
  - 部分端点缺 report_type / range / indexes / tab 等必填枚举

用法：
  HITHINK_FINANCE_API_KEY=<key> python scripts/probe_all_endpoints_pass2.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_all_endpoints import req, KEY, OUT, PROGRESS, SUMMARY, INTERVAL  # noqa

# 日期 → 毫秒时间戳
def ms(datestr, end=False):
    """'2025-09-01' → 毫秒时间戳（end=True 取当日 23:59:59）"""
    import datetime as dt
    d = dt.datetime.strptime(datestr, "%Y-%m-%d")
    if end:
        d = d.replace(hour=23, minute=59, second=59)
    return int(d.timestamp() * 1000)


# ── 补测参数（全部来自官方文档参数表） ──
P2 = {
    # A 股行情：start/end 必须毫秒
    "/api/a-share/prices/historical": {
        "thscode": "600519.SH", "interval": "1d",
        "start": ms("2025-09-01"), "end": ms("2025-09-30", end=True), "adjust": "forward"},
    "/api/a-share/special-data/hot-stock-list-history": {"date": ms("2025-09-30")},
    "/api/a-share/special-data/hot-stock-rank-trend": {
        "thscode": "600519.SH",
        "start_date": ms("2025-09-01"), "end_date": ms("2025-09-30", end=True)},
    # 指数历史
    "/api/a-share-index/prices/historical": {
        "thscode": "000300.SH", "interval": "1d",
        "start": ms("2025-09-01"), "end": ms("2025-09-30", end=True)},
    # 基金
    "/api/fund/market/historical": {
        "thscode": "510300.SH",
        "start": ms("2025-09-01"), "end": ms("2025-09-30", end=True)},
    "/api/fund/performance/indicators-historical": {
        "thscode": "510300.SH",
        "start": ms("2025-09-01"), "end": ms("2025-09-30", end=True)},
    "/api/fund/portfolio/stock-history": {
        "thscode": "510300.SH", "report_type": "quarterly", "end_date": ms("2025-09-30")},
    "/api/fund/portfolio/bond-history": {
        "thscode": "510300.SH", "report_type": "quarterly", "end_date": ms("2025-09-30")},
    "/api/fund/managers/performance": {"manager_id": "30000000", "range": "1y"},
    "/api/fund/indicators/line": {"indexes": "nav", "time_range": "1y"},
    "/api/fund/offerings/list": {"subscribe": "1"},
    "/api/fund/quota/list": {"tab": "all"},
    "/api/fund/quota/summary": {"tab": "all"},
    "/api/fund/backtest/result": {
        "thscode": "510300.SH", "buy_conditions": "1", "sell_conditions": "1",
        "buy_frequency_type": "1", "max_buy_times": "10", "per_buy_amount": "1000"},
    # 期货
    "/api/futures/contracts/detail": {"thscode": "IF2603.CFE"},
    "/api/futures/prices/daily": {"thscode": "IF2603.CFE",
                                  "start": "2025-09-01", "end": "2025-09-30"},
    "/api/futures/prices/intraday": {"thscode": "IF2603.CFE"},
    "/api/futures/positions/variety-daily": {"date": "2025-09-30"},
    "/api/futures/positions/company-variety-daily": {"date": "2025-09-30", "varieties": "IF"},
    "/api/futures/positions/contract-daily": {"thscode": "IF2603.CFE", "variety": "IF", "date": "2025-09-30"},
    "/api/futures/positions/contract-historical": {
        "thscode": "IF2603.CFE", "variety": "IF", "company": "中信期货", "start_date": "2025-09-01"},
    "/api/futures/warehouse-receipts/historical": {
        "thscode": "IF2603.CFE", "start_date": "2025-09-01", "end_date": "2025-09-30"},
    "/api/futures/basis/historical": {"thscode": "IF2603.CFE"},
    "/api/futures/calendar/session-timeline": {"thscode": "IF2603.CFE"},
    "/api/futures/calendar/trading-schedule": {
        "thscode": "IF2603.CFE", "start_date": "2025-09-01", "end_date": "2025-09-30"},
    # 期权
    "/api/options/contracts/detail": {"thscode": "10008616.SH"},
    "/api/options/prices/daily": {"thscode": "10008616.SH"},
    "/api/options/prices/intraday": {"thscode": "10008616.SH"},
    "/api/options/calendar/session-timeline": {"thscode": "10008616.SH"},
}


def main():
    if len(KEY) < 8:
        sys.exit("❌ 缺 HITHINK_FINANCE_API_KEY 环境变量")
    os.makedirs(OUT, exist_ok=True)
    done = json.load(open(PROGRESS)) if os.path.exists(PROGRESS) else {}

    print(f"补测 {len(P2)} 个端点（第一遍缺参/参数错误）\n" + "=" * 78)
    for i, (path, params) in enumerate(P2.items(), 1):
        print(f"[{i:>2}/{len(P2)}] {path}")
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
        elif code == 2003:
            rec["status"] = "🚫无权限"
        elif code == 1001:
            rec["status"] = "⚠️缺参数"
        elif code == 1002:
            rec["status"] = "⚠️参数格式"
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
    print(f"补测完成 · 全 {len(done)} 个端点最终状态：")
    for st in sorted(summary):
        print(f"  {st:<16} {len(summary[st]):>3} 个")


if __name__ == "__main__":
    main()

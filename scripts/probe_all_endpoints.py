#!/usr/bin/env python3
"""同花顺金融数据 API —— 全端点可用性普查（只读，零风险）

目的（对应 020_TDX 的探测方式）：
  1. 逐个实测官方文档中的 92 个端点，判定「免费到底能拿到哪些数据品种」
  2. 区分：可用(code=0) / 客户端专用(code=2004) / 无权限(2003) / 参数错误(1001) / 其他
  3. 每个可用端点落盘一份样本 JSON 到 sample_data/，供后续数据质量对拍

设计原则：
  - 只读不写生产数据；不打印 API Key
  - 每请求间隔 0.25s（官方明确声明不限累计次数，但需控制频率避免 4001）
  - 遇 4001 限流自动退避重试
  - 输出 progress.json 支持断点续跑

用法：
  HITHINK_FINANCE_API_KEY=<key> python scripts/probe_all_endpoints.py
"""
import json
import os
import sys
import time
import traceback
import requests

BASE = "https://fuyao.aicubes.cn"
KEY = os.environ.get("HITHINK_FINANCE_API_KEY", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sample_data")
PROGRESS = os.path.join(ROOT, "data", "probe_progress.json")
SUMMARY = os.path.join(ROOT, "data", "endpoint_availability.json")
INTERVAL = 0.25  # 秒

# ── 每个端点的最小合法参数（来自官方文档的参数表） ──
# 值说明：thscode=贵州茅台(主板/财务齐全)，000001.SZ=平安银行，510300.SH=沪深300ETF
P = {
    # A 股 · 行情
    "/api/a-share/prices/snapshot": {"thscodes": "600519.SH,000001.SZ"},
    "/api/a-share/prices/historical": {"thscode": "600519.SH", "start": "20250901", "end": "20250930"},
    "/api/a-share/valuations/snapshot": {"thscodes": "600519.SH"},
    "/api/a-share/calendar/trading-days": {},
    "/api/a-share/corporate-actions/adjustment-factors": {"thscode": "600519.SH"},
    # A 股 · 财务
    "/api/a-share/financials/income-statements": {"thscode": "600519.SH", "period": "quarterly", "limit": 4},
    "/api/a-share/financials/balance-sheets": {"thscode": "600519.SH", "period": "quarterly", "limit": 4},
    "/api/a-share/financials/cash-flow-statements": {"thscode": "600519.SH", "period": "quarterly", "limit": 4},
    "/api/a-share/financials/indicators": {"thscode": "600519.SH", "report": "2025-1"},
    # A 股 · 标注未开放（验证是否真不可调）
    "/api/a-share/capital-flow/snapshot": {"thscode": "600519.SH"},
    "/api/a-share/capital-flow/historical": {"thscode": "600519.SH", "interval": "1d"},
    "/api/a-share/high-frequency/intraday": {"thscode": "600519.SH"},
    "/api/a-share/high-frequency/historical": {"thscode": "600519.SH"},
    "/api/news/events/search": {"q": "茅台"},
    # A 股 · 集合竞价
    "/api/a-share/auction/snapshot": {"thscodes": "600519.SH"},
    "/api/a-share/auction/short-term-benchmark": {},
    # A 股 · 特色数据
    "/api/a-share/special-data/dragon-tiger-list": {"start_date": "20250901", "end_date": "20250930"},
    "/api/a-share/special-data/limit-up-pool": {"date": "20250930"},
    "/api/a-share/special-data/limit-down-pool": {"date": "20250930"},
    "/api/a-share/special-data/limit-break-pool": {"date": "20250930"},
    "/api/a-share/special-data/limit-up-ladder": {},
    "/api/a-share/special-data/hot-stock-list": {},
    "/api/a-share/special-data/hot-stock-list-history": {"start_date": "20250901", "end_date": "20250930"},
    "/api/a-share/special-data/hot-stock-rank-trend": {"thscode": "600519.SH", "start_date": "20250901", "end_date": "20250930"},
    "/api/a-share/special-data/anomaly-analysis-list": {"start_date": "20250901", "end_date": "20250930"},
    "/api/a-share/special-data/anomaly-analysis-stock": {"thscodes": "600519.SH", "start_date": "20250901", "end_date": "20250930"},
    "/api/a-share/special-data/skyrocket-list": {},
    # 指数
    "/api/a-share-index/catalog/ths-index-list": {"index_type": "concept"},
    "/api/a-share-index/constituents/ths-stock-list": {"thscode": "000300.SH"},
    "/api/a-share-index/prices/snapshot": {"thscodes": "000300.SH"},
    "/api/a-share-index/prices/historical": {"thscode": "000300.SH", "start": "20250901", "end": "20250930"},
    # 标的
    "/api/meta/tickers/list": {"asset_type": "a-share", "limit": 5},
    "/api/meta/tickers/search": {"q": "茅台"},
    # 全市场导出
    "/api/dump/market-dumps/daily-k/download-url": {},
    "/api/dump/market-dumps/daily-k-10d/download-url": {},
    "/api/dump/market-dumps/adjustment-factors/download-url": {},
    # ── 基金域 ──
    "/api/fund/profile/detail": {"thscode": "510300.SH"},
    "/api/fund/market/snapshot": {"thscode": "510300.SH"},
    "/api/fund/market/historical": {"thscode": "510300.SH", "start": "20250901", "end": "20250930"},
    "/api/fund/performance/nav": {"thscode": "510300.SH"},
    "/api/fund/performance/returns": {"thscode": "510300.SH"},
    "/api/fund/performance/drawdowns": {"thscode": "510300.SH"},
    "/api/fund/performance/indicators-historical": {"thscode": "510300.SH"},
    "/api/fund/portfolio/holdings": {"thscode": "510300.SH"},
    "/api/fund/portfolio/stock-history": {"thscode": "510300.SH"},
    "/api/fund/portfolio/stock-report-dates": {"thscode": "510300.SH"},
    "/api/fund/portfolio/bond-history": {"thscode": "510300.SH"},
    "/api/fund/portfolio/bond-report-dates": {"thscode": "510300.SH"},
    "/api/fund/portfolio/industry-allocation": {"thscode": "510300.SH"},
    "/api/fund/portfolio/asset-allocation": {"thscode": "510300.SH"},
    "/api/fund/holders/detail": {"thscode": "510300.SH"},
    "/api/fund/holders/top": {"thscode": "510300.SH"},
    "/api/fund/managers/detail": {"manager_id": "30000000"},
    "/api/fund/managers/experience": {"manager_id": "30000000"},
    "/api/fund/managers/performance": {"manager_id": "30000000"},
    "/api/fund/managers/investment-style": {"manager_id": "30000000"},
    "/api/fund/companies/detail": {"company_id": "80000226"},
    "/api/fund/corporate-actions/dividends": {"thscode": "510300.SH"},
    "/api/fund/financials/indicators": {"thscode": "510300.SH"},
    "/api/fund/financials/income-statements": {"thscode": "510300.SH"},
    "/api/fund/financials/balance-sheets": {"thscode": "510300.SH"},
    "/api/fund/diagnostics/detail": {"thscode": "510300.SH"},
    "/api/fund/news/article-list": {"thscode": "510300.SH"},
    "/api/fund/offerings/list": {},
    "/api/fund/indicators/line": {"thscode": "510300.SH"},
    "/api/fund/indicators/table": {"thscode": "510300.SH"},
    "/api/fund/backtest/indicators": {},
    "/api/fund/backtest/result": {},
    "/api/fund/quota/list": {},
    "/api/fund/quota/summary": {},
    # ── 期货域（记录能力边界，本项目不急用） ──
    "/api/futures/varieties/list": {},
    "/api/futures/variety-plates/list": {},
    "/api/futures/contracts/list": {},
    "/api/futures/contracts/detail": {},
    "/api/futures/contracts/main-list": {},
    "/api/futures/contracts/main-continuous-list": {},
    "/api/futures/contracts/secondary-main-list": {},
    "/api/futures/contracts/commodity-index-list": {},
    "/api/futures/prices/daily": {},
    "/api/futures/prices/intraday": {},
    "/api/futures/positions/company-list": {},
    "/api/futures/positions/company-variety-daily": {},
    "/api/futures/positions/contract-daily": {},
    "/api/futures/positions/contract-historical": {},
    "/api/futures/positions/variety-daily": {},
    "/api/futures/warehouse-receipts/historical": {},
    "/api/futures/basis/historical": {},
    "/api/futures/basis/main-continuous-latest": {},
    "/api/futures/calendar/session-timeline": {},
    "/api/futures/calendar/trading-schedule": {},
    "/api/futures/fundamentals/indicators-historical": {},
    # ── 期权域 ──
    "/api/options/varieties/list": {},
    "/api/options/contracts/list": {},
    "/api/options/contracts/detail": {},
    "/api/options/prices/daily": {},
    "/api/options/prices/intraday": {},
    "/api/options/calendar/session-timeline": {},
}


def req(path, params=None, retries=2):
    """发一次请求；遇 4001 限流退避重试。"""
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            r = requests.get(BASE + path, params=params or {},
                             headers={"X-api-key": KEY}, timeout=30)
            dt = (time.time() - t0) * 1000
            try:
                j = r.json()
            except Exception:
                return {"code": "HTML", "message": r.text[:100]}, r.status_code, dt
            if isinstance(j, dict) and j.get("code") == 4001 and attempt < retries:
                wait = 2 ** attempt * 2
                print(f"      [限流 4001] 退避 {wait}s 重试…")
                time.sleep(wait)
                continue
            return j, r.status_code, dt
        except Exception as e:
            if attempt < retries:
                time.sleep(2)
                continue
            return {"code": "EXC", "message": str(e)[:120]}, None, (time.time() - t0) * 1000
    return {"code": "EXC", "message": "retries exhausted"}, None, 0


def main():
    if len(KEY) < 8:
        sys.exit("❌ 缺 HITHINK_FINANCE_API_KEY 环境变量")
    os.makedirs(OUT, exist_ok=True)

    # 断点续跑：加载已完成的普查结果
    done = {}
    if os.path.exists(PROGRESS):
        done = json.load(open(PROGRESS))
        print(f"↻ 断点续跑：已有 {len(done)} 个端点结果，跳过\n")

    paths = list(P)
    print(f"共 {len(paths)} 个端点待普查\n" + "=" * 78)

    for i, path in enumerate(paths, 1):
        if path in done:
            continue
        print(f"[{i:>2}/{len(paths)}] {path}")
        j, http, dt = req(path, P[path])
        code = j.get("code") if isinstance(j, dict) else "?"
        msg = str(j.get("message", ""))[:60] if isinstance(j, dict) else ""

        rec = {"http": http, "code": code, "message": msg, "latency_ms": round(dt), "params": P[path]}

        if code == 0:
            d = j.get("data", {})
            # 统计返回规模
            if isinstance(d, dict):
                item = d.get("item")
                if isinstance(item, list):
                    rec["n_items"] = len(item)
                    if item and isinstance(item[0], dict):
                        rec["sample_fields"] = sorted(item[0].keys())
                rec["data_keys"] = sorted(d.keys())[:20]
            rec["status"] = "✅可用"
            # 落盘样本（截断超长响应）
            safe = path.strip("/").replace("/", "__")
            with open(os.path.join(OUT, f"{safe}.json"), "w") as f:
                json.dump({"path": path, "params": P[path], "data": d}, f,
                          ensure_ascii=False, indent=1, default=str)
        elif code == 2004:
            rec["status"] = "⛔客户端专用"
        elif code == 2003:
            rec["status"] = "🚫无权限"
        elif code == 1001:
            rec["status"] = "⚠️缺参数"
        elif code == 3001:
            rec["status"] = "⚠️标的不存在"
        elif code == 3002:
            rec["status"] = "⚠️数据未就绪"
        elif code == 3004:
            rec["status"] = "⚠️类型不支持"
        else:
            rec["status"] = f"❓{code}"

        n = rec.get("n_items", "-")
        print(f"      {rec['status']}  HTTP={http} code={code} {round(dt):>4}ms items={n}  {msg}")

        done[path] = rec
        json.dump(done, open(PROGRESS, "w"), ensure_ascii=False, indent=1)
        time.sleep(INTERVAL)

    # ── 汇总 ──
    summary = {}
    for p, r in done.items():
        summary.setdefault(r["status"], []).append(p)
    json.dump(summary, open(SUMMARY, "w"), ensure_ascii=False, indent=1)

    print("\n" + "=" * 78)
    print("普查完成 · 按状态汇总：")
    for st in sorted(summary):
        print(f"  {st:<14} {len(summary[st]):>3} 个")
    print(f"\n明细: {SUMMARY}\n样本: {OUT}/")


if __name__ == "__main__":
    main()

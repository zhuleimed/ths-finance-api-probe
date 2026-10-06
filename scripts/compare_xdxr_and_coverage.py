#!/usr/bin/env python3
"""① 23财务指标的全市场覆盖率抽样  ② 除权除息(分红送转)对拍

用法：
  HITHINK_FINANCE_API_KEY=<key> python scripts/compare_xdxr_and_coverage.py --n 60
"""
import argparse
import json
import os
import random
import sqlite3
import sys
import time

import pandas as pd
import requests

BASE = "https://fuyao.aicubes.cn"
KEY = os.environ.get("HITHINK_FINANCE_API_KEY", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/extra_features"
DB = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db"

ALL_IDS = [
    # growth (4)
    "calculate_operating_income_yoy_growth_ratio", "calculate_operating_profit_yoy_growth_ratio",
    "total_assets_growth_ratio", "calculate_parent_holder_net_profit_yoy_growth_ratio",
    # profitability (5)
    "sale_gross_margin", "sale_net_interest_ratio", "total_assets_net_ratio",
    "index_deduct_weighted_avg_roe", "index_weighted_avg_roe",
    # solvency (5)
    "current_ratio", "quick_ratio", "assets_debt_ratio", "cash_ratio",
    "earned_interest_multiple",
    # operation (5)
    "long_term_debt_equity_ratio", "total_assets_turnover_ratio", "inventory_turnover_ratio",
    "current_assets_turnover_ratio", "receive_account_turnover_ratio",
    # cash-flow (4)
    "cash_operating_index", "operating_cash_flow_net_divide_income",
    "net_profit_cash_content", "cash_meet_invest_ratio",
]


def thscode(c):
    if c.startswith(("60", "68", "90")): return c + ".SH"
    if c.startswith(("00", "30", "20")): return c + ".SZ"
    return c + ".BJ"


def get_indicators(code, report="2025-1"):
    try:
        r = requests.get(f"{BASE}/api/a-share/financials/indicators",
                         params={"thscode": thscode(code), "report": report},
                         headers={"X-api-key": KEY}, timeout=30).json()
    except Exception as e:
        return None, str(e)
    if r.get("code") != 0:
        return None, f"code={r.get('code')} {r.get('message','')[:40]}"
    out = {}
    for ab in r.get("data", {}).get("abilities", []):
        for ind in ab.get("indicators", []):
            out[ind["index_id"]] = ind.get("value")
    return out, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    args = ap.parse_args()
    if len(KEY) < 8:
        sys.exit("❌ 缺 HITHINK_FINANCE_API_KEY")

    # ── ① 覆盖率抽样 ──
    print("=" * 78)
    print(f"① 23 财务指标覆盖率抽样（随机 {args.n} 只，报告期 2025-1）")
    print("=" * 78)
    conn = sqlite3.connect(DB)
    codes = [r[0] for r in conn.execute("SELECT symbol FROM stock_list").fetchall()]
    conn.close()
    random.seed(42)
    sample = random.sample(codes, min(args.n, len(codes)))

    rows, fails = [], []
    for i, c in enumerate(sample, 1):
        ind, err = get_indicators(c)
        if ind is None:
            fails.append((c, err)); continue
        filled = {k: (v not in (None, "", "null")) for k, v in ind.items()}
        rows.append({"code": c, "n_returned": len(ind),
                     "n_nonnull": sum(filled.values()), **{k: int(v) for k, v in filled.items()}})
        if i % 20 == 0:
            print(f"    …{i}/{len(sample)}")
        time.sleep(0.2)

    if fails:
        print(f"\n  拉取失败 {len(fails)} 只，样例 {fails[:5]}")
    df = pd.DataFrame(rows)
    if df.empty:
        sys.exit("❌ 无数据")
    print(f"\n  成功 {len(df)} 只")
    print(f"  平均返回指标数: {df['n_returned'].mean():.1f} / 23")
    print(f"  平均非空指标数: {df['n_nonnull'].mean():.1f} / 23")
    print(f"  23 个全非空的股票: {(df['n_nonnull']==23).sum()} / {len(df)}")
    print(f"\n  逐指标非空率（低=该指标覆盖差）：")
    cov = []
    for k in ALL_IDS:
        if k in df.columns:
            cov.append((k, df[k].mean() * 100))
    for k, v in sorted(cov, key=lambda x: x[1]):
        bar = "█" * int(v / 5)
        print(f"    {k:<46} {v:5.1f}%  {bar}")

    json.dump({"sample_n": len(df), "mean_returned": float(df['n_returned'].mean()),
               "mean_nonnull": float(df['n_nonnull'].mean()),
               "coverage": {k: v for k, v in cov}},
              open(os.path.join(ROOT, "data", "indicator_coverage.json"), "w"),
              ensure_ascii=False, indent=1)

    # ── ② 除权除息对拍 ──
    print("\n" + "=" * 78)
    print("② 除权除息对拍：同花顺 corporate-actions  vs  004 xdxr(mootdx)")
    print("=" * 78)
    for code in ["000001", "600519", "000858"]:
        r = requests.get(f"{BASE}/api/a-share/corporate-actions/adjustment-factors",
                         params={"thscode": thscode(code)},
                         headers={"X-api-key": KEY}, timeout=30).json()
        if r.get("code") != 0:
            print(f"  {code}: 接口失败 {r.get('code')} {r.get('message')}"); continue
        items = r.get("data", {}).get("item", [])
        fx = os.path.join(FE, "xdxr", f"{code}.parquet")
        if not os.path.exists(fx):
            print(f"  {code}: 004 无 xdxr"); continue
        d4 = pd.read_parquet(fx)
        print(f"\n  {code}: 同花顺 {len(items)} 条事件 | 004(mootdx) {len(d4)} 条")
        if items:
            print(f"    同花顺字段: {sorted(items[0].keys())}")
            ex = items[0]
            dd = f"{ex.get('ex_date', '')}"
            print(f"    最新事件样例: ex_date={ex.get('ex_date')} "
                  f"每股分红={ex.get('dividend_per_share')} 送股={ex.get('per_share_bonus')} "
                  f"配股比例={ex.get('allotment_ratio')} 配股价={ex.get('allotment_price')}")
        # 按除权日对拍
        if items and len(d4):
            ths_dates = set()
            for it in items:
                d = it.get("ex_date") or it.get("ex_date_ms")
                if isinstance(d, (int, float)):
                    d = pd.to_datetime(d, unit="ms", utc=True).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d")
                ths_dates.add(str(d)[:10])
            x4 = d4.copy()
            x4["date"] = pd.to_datetime(
                dict(year=x4["year"], month=x4["month"], day=x4["day"]),
                errors="coerce").dt.strftime("%Y-%m-%d")
            d4_dates = set(x4["date"].dropna())
            both = ths_dates & d4_dates
            print(f"    除权日交集: {len(both)}  仅同花顺: {len(ths_dates-d4_dates)}  "
                  f"仅004: {len(d4_dates-ths_dates)}")
            if ths_dates - d4_dates:
                print(f"      仅同花顺有(004缺): {sorted(ths_dates-d4_dates)[:8]}")
            if d4_dates - ths_dates:
                print(f"      仅004有(同花顺缺): {sorted(d4_dates-ths_dates)[:8]}")
        time.sleep(0.25)


if __name__ == "__main__":
    main()

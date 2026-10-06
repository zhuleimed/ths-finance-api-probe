#!/usr/bin/env python3
"""财务数据对拍：同花顺官方 API  vs  004_sequoia-x 现有 finance（akshare 同花顺摘要）

004 的 finance 来源是 akshare `stock_financial_abstract_ths`（网页抓取版同花顺摘要），
同花顺官方 API 是同一家数据源的**官方规范版**。本脚本回答两个问题：

  Q1 两者重叠字段是否一致？（决定能否安全替换，防破坏历史缓存）
  Q2 官方 API 多了哪些字段？（决定增补价值）

用法：
  HITHINK_FINANCE_API_KEY=<key> python scripts/compare_finance_ths_vs_004.py --codes 000001,600519,000858
"""
import argparse
import glob
import json
import os
import sys
import time

import pandas as pd
import requests

BASE = "https://fuyao.aicubes.cn"
KEY = os.environ.get("HITHINK_FINANCE_API_KEY", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/extra_features"

# 同花顺官方指标 index_id → 004 finance 列名（同口径方可比）
OVERLAP = {
    "net_profit_yoy_growth_ratio": "净利润同比增长率",
    "operating_income_yoy_growth_ratio": "营业总收入同比增长率",
    "sale_net_interest_ratio": "销售净利率",
    "index_weighted_avg_roe": "净资产收益率",
    "current_ratio": "流动比率",
    "quick_ratio": "速动比率",
    "assets_debt_ratio": "资产负债率",
}
# 004 已有的 23 个字段（用于算「增补」）
EXISTING_004 = ["净利润", "净利润同比增长率", "扣非净利润", "扣非净利润同比增长率",
                "营业总收入", "营业总收入同比增长率", "基本每股收益", "每股净资产",
                "每股资本公积金", "每股未分配利润", "每股经营现金流", "销售净利率",
                "净资产收益率", "净资产收益率-摊薄", "营业周期", "应收账款周转天数",
                "流动比率", "速动比率", "保守速动比率", "产权比率", "资产负债率"]


def thscode(code: str) -> str:
    if code.startswith(("60", "68", "90")):
        return code + ".SH"
    if code.startswith(("00", "30", "20")):
        return code + ".SZ"
    return code + ".BJ"


def fetch_indicators(code: str, report: str):
    """取某股某报告期的全部 5 类指标，拍平为 {index_id: value}"""
    r = requests.get(f"{BASE}/api/a-share/financials/indicators",
                     params={"thscode": thscode(code), "report": report},
                     headers={"X-api-key": KEY}, timeout=30).json()
    if r.get("code") != 0:
        return None, r
    out, names = {}, {}
    for ab in r.get("data", {}).get("abilities", []):
        for ind in ab.get("indicators", []):
            iid = ind.get("index_id") or ind.get("name")
            out[iid] = ind.get("value")
            names[iid] = ind.get("name")
    return out, names


def fetch_report_periods(code: str, limit: int = 12):
    """取该股最近 N 期财务指标报告期（用 income-statements 的 report 字段）"""
    r = requests.get(f"{BASE}/api/a-share/financials/indicators",
                     params={"thscode": thscode(code), "report": "2025-1"},
                     headers={"X-api-key": KEY}, timeout=30).json()
    return r


def to_pct(v):
    """同花顺指标单位可能是小数(0.15)或百分数(15.0)，统一成百分数"""
    try:
        f = float(str(v).replace("%", ""))
    except Exception:
        return None
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="000001,600519,000858,300750,601318")
    ap.add_argument("--reports", default="2023-4,2024-1,2024-2,2024-3,2024-4,2025-1")
    args = ap.parse_args()

    if len(KEY) < 8:
        sys.exit("❌ 缺 HITHINK_FINANCE_API_KEY")
    codes = [c.strip() for c in args.codes.split(",")]
    reports = [r.strip() for r in args.reports.split(",")]
    # 同花顺 report 编码（yyyy-N）→ 004 的「报告期」日期
    rep_key = {}
    for y in range(2015, 2031):
        rep_key[f"{y}-1"] = f"{y}-03-31"
        rep_key[f"{y}-2"] = f"{y}-06-30"
        rep_key[f"{y}-3"] = f"{y}-09-30"
        rep_key[f"{y}-4"] = f"{y}-12-31"

    print("=" * 78)
    print("Q1 重叠字段一致性对拍")
    print("=" * 78)
    all_rows = []
    all_ids = set()
    for code in codes:
        f004 = os.path.join(FE, "finance", f"{code}.parquet")
        if not os.path.exists(f004):
            print(f"  {code}: ⚠️ 004 无 finance 文件，跳过")
            continue
        d4 = pd.read_parquet(f004)
        print(f"\n{code}  004 有 {len(d4)} 期财务 (最新 {d4['报告期'].max()})")

        for rep in reports:
            ind, names = fetch_indicators(code, rep)
            if not ind:
                print(f"    {rep}: 接口无数据")
                continue
            all_ids |= set(ind.keys())
            dt = rep_key.get(rep)
            row4 = d4[d4["报告期"] == dt]
            line = {"code": code, "report": rep, "date": dt}
            for iid, col4 in OVERLAP.items():
                v_ths = to_pct(ind.get(iid))
                v_004 = to_pct(row4[col4].iloc[0]) if len(row4) and col4 in row4.columns else None
                line[f"{iid}|ths"] = v_ths
                line[f"{iid}|004"] = v_004
                if v_ths is not None and v_004 is not None:
                    line[f"{iid}|diff"] = abs(v_ths - v_004)
            all_rows.append(line)
            time.sleep(0.25)

    df = pd.DataFrame(all_rows)
    if df.empty:
        sys.exit("❌ 无对拍数据")

    print("\n  逐指标一致性（同口径比较，单位=百分数）：")
    print(f"  {'指标':<34} {'可比期数':>7} {'一致(<0.01)':>11} {'差异<1%':>9} {'中位差':>10}")
    for iid, col4 in OVERLAP.items():
        a, b = df.get(f"{iid}|ths"), df.get(f"{iid}|004")
        if a is None or b is None:
            continue
        ok = a.notna() & b.notna()
        if ok.sum() == 0:
            print(f"  {iid:<34} {0:>7}  —")
            continue
        diff = (a[ok] - b[ok]).abs()
        print(f"  {iid:<34} {ok.sum():>7} {(diff < 0.01).sum():>11} "
              f"{(diff < 1).sum():>9} {diff.median():>10.4f}")

    print("\n" + "=" * 78)
    print("Q2 增补价值：同花顺官方 API 提供的全部指标 vs 004 已有")
    print("=" * 78)
    # 拉一次全量字段名
    ind, names = fetch_indicators(codes[0], "2025-1")
    if names:
        official = set(ind.keys()) if ind else set()
        print(f"  同花顺官方指标总数: {len(official)}")
        # 004 已有的可比指标
        have = set(OVERLAP.keys())
        print(f"  其中 004 已覆盖(同口径): {len(have)}  → {sorted(have)}")
        new = sorted(official - have)
        print(f"  ★ 004 完全没有的: {len(new)}")
        for i in new:
            print(f"      - {i:<44} {names.get(i,'')}")

    out = os.path.join(ROOT, "data", "finance_compare.json")
    df.to_json(out, orient="records", force_ascii=False, indent=1)
    print(f"\n明细: {out}")


if __name__ == "__main__":
    main()

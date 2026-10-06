#!/usr/bin/env python3
"""逐行定位 004 中「复权口径不符约定」的行（约定：004 应为不复权）。

★ 方法论修正（2026-10-06）：
    早先版本用「全窗比值中位数」判定，被正确段稀释 → 漏报。
    实测发现真实形态是**分段**的，例如 002762：
        2020-2023 r=1.0000（正确）
        2024-2025 r=5.6268→5.6725（复权价，错）
        2026      r 逐步回到 1.0000
    正确判据：**逐行**比较，r 偏离 1 超过容差的即为问题行，与年份/段无关。

用法：
  python scripts/find_adjusted_rows.py [--tol 0.001]
"""
import argparse
import json
import os
import sqlite3

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(ROOT, "data", "dump", "daily-k.parquet")
DB = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db"
OUT = os.path.join(ROOT, "data", "adjusted_rows_to_fix.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=0.001,
                    help="比值偏离 1 的容差（默认 0.1%%）")
    args = ap.parse_args()

    print("=" * 78)
    print(f"逐行定位复权口径错误 · 容差 |r-1| > {args.tol}")
    print("=" * 78)

    t = pd.read_parquet(DUMP, columns=["thscode", "date_ms", "close_price"])
    t["symbol"] = t["thscode"].str.split(".").str[0]
    t["date"] = (pd.to_datetime(t["date_ms"], unit="ms", utc=True)
                 .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    t = t[["symbol", "date", "close_price"]].rename(columns={"close_price": "ths"})

    conn = sqlite3.connect(DB)
    d = pd.read_sql("SELECT symbol,date,close AS db FROM stock_daily", conn)
    conn.close()

    m = t.merge(d, on=["symbol", "date"], how="inner")
    m = m[m["ths"] > 0].copy()
    m["r"] = m["db"] / m["ths"]
    m["bad"] = (m["r"] - 1).abs() > args.tol
    print(f"比对 {len(m):,} 行 / {m['symbol'].nunique():,} 只")
    print(f"偏离行 {m['bad'].sum():,} 行 ({m['bad'].mean()*100:.3f}%) / "
          f"{m.loc[m['bad'],'symbol'].nunique():,} 只")

    b = m[m["bad"]]
    if not len(b):
        print("✅ 无异常，全部符合不复权约定")
        return

    # 按股票汇总
    g = b.groupby("symbol").agg(n_bad=("bad", "size"),
                                rmin=("r", "min"), rmax=("r", "max"),
                                dmin=("date", "min"), dmax=("date", "max"))
    g["n_total"] = m.groupby("symbol").size()
    g["frac"] = g["n_bad"] / g["n_total"]
    g = g.sort_values("n_bad", ascending=False)
    print(f"\n【按股票汇总 · 前 25】")
    print(f"  {'代码':<9}{'坏行':>7}{'总行':>7}{'占比':>8}{'r_min':>9}{'r_max':>9}  区间")
    for s, r in g.head(25).iterrows():
        print(f"  {s:<9}{r['n_bad']:>7.0f}{r['n_total']:>7.0f}{r['frac']*100:>7.1f}%"
              f"{r['rmin']:>9.4f}{r['rmax']:>9.4f}  {r['dmin']}~{r['dmax']}")

    # 按年份看分布（帮助判断波及范围）
    b2 = b.copy()
    b2["yr"] = b2["date"].str[:4]
    print(f"\n【坏行按年份分布】")
    yc = b2.groupby("yr").agg(行数=("bad", "size"), 股票数=("symbol", "nunique"))
    for y, r in yc.iterrows():
        print(f"  {y}  {r['行数']:>7.0f} 行  {r['股票数']:>5.0f} 只")

    # 分类：这些股票是"整段复权"还是"个别行错"
    strong = g[g["frac"] > 0.5]
    weak = g[g["frac"] <= 0.5]
    print(f"\n【分类】")
    print(f"  大部分行都错（frac>50%，属整段复权）: {len(strong)} 只 / {strong['n_bad'].sum():.0f} 行")
    print(f"  少数行错（frac<=50%，属个别/边界）  : {len(weak)} 只 / {weak['n_bad'].sum():.0f} 行")
    if len(weak):
        print(f"\n  少数行错的样例（前 10，多为单点差异）：")
        print(weak.head(10).to_string())

    json.dump({
        "tol": args.tol,
        "n_bad_rows": int(m["bad"].sum()),
        "n_bad_stocks": int(m.loc[m["bad"], "symbol"].nunique()),
        "by_year": {k: int(v) for k, v in b2.groupby("yr").size().items()},
        "strong_stocks": strong.reset_index().to_dict("records"),
        "weak_stocks": weak.reset_index().to_dict("records"),
    }, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(f"\n明细: {OUT}")


if __name__ == "__main__":
    main()

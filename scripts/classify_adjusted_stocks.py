#!/usr/bin/env python3
"""精确定位 004 中「复权口径与约定不符」的股票（约定：004 应为不复权）。

判定方法：与同花顺不复权 dump 逐日比对，算比值 r = 004 / 同花顺不复权。
    r ≡ 1         → 正常（不复权），符合约定
    r 恒定 ≠ 1     → 整段是复权价（纯后复权行为）
    r 分段恒定     → 前复权（在每个除权日跳变）
    r 乱跳         → 混口径或独立错误

输出：每只股票的比值序列统计 + 分类，供修复决策。

用法：
  python scripts/classify_adjusted_stocks.py [--start 2016-10-09]
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
OUT = os.path.join(ROOT, "data", "adjusted_stock_classification.json")
MIN_ROWS = 50          # 少于这个行数不判定
TOL = 1e-3             # |r-1| < TOL 视为「等于 1」
CV_TOL = 1e-3          # 变异系数 < CV_TOL 视为「恒定」


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-10-09", help="比对窗口起点（同花顺 dump 覆盖范围）")
    ap.add_argument("--end", default="2026-09-30")
    args = ap.parse_args()

    print("=" * 78)
    print(f"004 复权口径分类 · 窗口 {args.start} ~ {args.end}")
    print("=" * 78)

    t = pd.read_parquet(DUMP, columns=["thscode", "date_ms", "close_price"])
    t["symbol"] = t["thscode"].str.split(".").str[0]
    t["date"] = (pd.to_datetime(t["date_ms"], unit="ms", utc=True)
                 .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    t = t[["symbol", "date", "close_price"]].rename(columns={"close_price": "ths"})
    print(f"同花顺侧 {len(t):,} 行 / {t['symbol'].nunique():,} 只")

    conn = sqlite3.connect(DB)
    d = pd.read_sql("SELECT symbol,date,close AS db FROM stock_daily "
                    "WHERE date>=? AND date<=?",
                    conn, params=(args.start, args.end))
    print(f"004 侧   {len(d):,} 行 / {d['symbol'].nunique():,} 只")

    m = t.merge(d, on=["symbol", "date"], how="inner")
    print(f"交集     {len(m):,} 行 / {m['symbol'].nunique():,} 只")
    if not len(m):
        raise SystemExit("无交集")

    # 排除 ths 为 0 导致的除零
    m = m[m["ths"] > 0].copy()
    m["r"] = m["db"] / m["ths"]

    g = m.groupby("symbol")["r"].agg(
        n="size", median="median", std="std", q10=lambda s: s.quantile(.10),
        q90=lambda s: s.quantile(.90))
    g = g[g["n"] >= MIN_ROWS].copy()
    g["cv"] = g["std"] / g["median"].abs()
    g["spread"] = g["q90"] - g["q10"]     # 分段跳变会拉大这个

    def classify(r):
        if abs(r["median"] - 1) < TOL and r["cv"] < CV_TOL:
            return "①正常(不复权)"
        if abs(r["median"] - 1) >= TOL and r["cv"] < CV_TOL:
            return "②恒定复权(纯后复权)"
        if abs(r["median"] - 1) >= TOL and r["cv"] >= CV_TOL:
            return "③分段复权(前复权/混口径)"
        return "④近似1但轻微漂移(噪声)"

    g["cls"] = g.apply(classify, axis=1)
    print(f"\n可判定股票 {len(g):,} 只（行数 ≥ {MIN_ROWS}）")
    print("\n【分类结果】")
    vc = g["cls"].value_counts()
    for k in sorted(vc.index):
        print(f"  {k:<26} {vc[k]:>5} 只")

    print("\n【需修复的（②+③）】")
    bad = g[g["cls"].str.startswith(("②", "③"))].sort_values("median", ascending=False)
    print(f"  合计 {len(bad)} 只")
    if len(bad):
        print(f"\n  {'代码':<9}{'行数':>6}{'比值中位':>10}{'cv':>9}{'q90-q10':>9}  分类")
        for s, r in bad.head(40).iterrows():
            print(f"  {s:<9}{r['n']:>6.0f}{r['median']:>10.4f}{r['cv']:>9.4f}"
                  f"{r['spread']:>9.4f}  {r['cls']}")
        if len(bad) > 40:
            print(f"  … 其余 {len(bad)-40} 只见 JSON")

    print("\n【④近似1但轻微漂移的】—— 数值差异极小，是否处理待定")
    n4 = g[g["cls"].str.startswith("④")]
    print(f"  {len(n4)} 只；其中 |中位比值-1| 最大 = "
          f"{abs(n4['median']-1).max():.5f}" if len(n4) else "  0 只")

    # 逐日明细抽样：看看 ③ 类到底怎么跳的
    if len(bad):
        print("\n【③类抽样：比值随时间的形态（取 3 只，按季度聚合）】")
        c3 = bad[bad["cls"].str.startswith("③")].index[:3]
        for s in c3:
            sub = m[m["symbol"] == s].copy()
            sub["q"] = sub["date"].str[:7]
            qq = sub.groupby("q")["r"].agg(["median", "size"])
            print(f"\n  {s}（比值 = 004 / 同花顺不复权）:")
            for q, row in qq.iterrows():
                print(f"     {q}  r={row['median']:.4f}  n={row['size']:.0f}")

    json.dump({"window": [args.start, args.end],
               "counts": {k: int(v) for k, v in vc.items()},
               "to_fix": bad.reset_index()[["symbol", "n", "median", "cv", "spread", "cls"]]
               .to_dict("records"),
               "noise_like": n4.reset_index()[["symbol", "n", "median", "cv"]]
               .to_dict("records") if len(n4) else []},
              open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(f"\n明细: {OUT}")


if __name__ == "__main__":
    main()

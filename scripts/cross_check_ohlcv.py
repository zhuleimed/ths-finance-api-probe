#!/usr/bin/env python3
"""★跨源逐值对拍：同花顺 Parquet dump  vs  004_sequoia-x 现有行情库

这是判断「同花顺行情数据能不能用」的核心证据：
不只看行数，而是逐日比对 OHLCV **数值**，算差异率。

004 现有源（腾讯/新浪/baostock 三轨）与同花顺是**完全独立的数据面**，
若两者数值高度一致 → 数据可信，可作后备/主源。

用法：
  python scripts/cross_check_ohlcv.py [--sample 200] [--days 120]
"""
import argparse
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(ROOT, "data", "dump", "daily-k.parquet")
DB = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db"
OUT = os.path.join(ROOT, "data", "cross_check_report.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--days", type=int, default=120)
    args = ap.parse_args()

    print("读取同花顺 dump …")
    ths = pd.read_parquet(DUMP, columns=["thscode", "date_ms", "open_price",
                                          "high_price", "low_price", "close_price",
                                          "volume", "turnover"])
    # ★关键：date_ms 是「Asia/Shanghai 零点」的毫秒戳，直接按 UTC 转会整体偏前一天。
    # 必须先按 UTC 解释、再转上海时区，才能得到正确的交易日。
    ths["date"] = (pd.to_datetime(ths["date_ms"], unit="ms", utc=True)
                   .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    ths["symbol"] = ths["thscode"].str.split(".").str[0]
    ths = ths.drop(columns=["date_ms", "thscode"])

    conn = sqlite3.connect(DB)
    # 004 日期格式确认
    fmt = conn.execute("SELECT date FROM stock_daily LIMIT 1").fetchone()[0]
    print(f"004 日期格式样例: {fmt}")

    # 最近 N 个交易日窗口
    all_dates = sorted(ths["date"].unique())
    win = all_dates[-args.days:]
    print(f"对拍窗口: {win[0]} ~ {win[-1]} ({len(win)} 交易日)")

    # 抽样：从 004 库里取有数据的股票
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM stock_daily ORDER BY symbol LIMIT ?",
        (args.sample,)).fetchall()]
    print(f"抽样 {len(codes)} 只（004 库中代码顺序前 N 只）")

    t = ths[ths["symbol"].isin(codes) & ths["date"].isin(win)].copy()
    print(f"同花顺侧: {len(t):,} 行")

    ph = ",".join("?" * len(codes))
    dh = ",".join("?" * len(win))
    q = (f"SELECT symbol,date,open,high,low,close,volume,turnover "
         f"FROM stock_daily WHERE symbol IN ({ph}) AND date IN ({dh})")
    d = pd.read_sql(q, conn, params=codes + win)
    conn.close()
    print(f"004 侧  : {len(d):,} 行")

    m = t.merge(d, on=["symbol", "date"], how="inner",
                suffixes=("_ths", "_004"))
    print(f"交集    : {len(m):,} 行")

    only_ths = len(t) - len(m)
    only_004 = len(d) - len(m)
    print(f"\n【覆盖差异】同花顺独有 {only_ths:,} 行 | 004 独有 {only_004:,} 行")
    if only_ths:
        ex = t.merge(d[["symbol", "date"]], on=["symbol", "date"], how="left", indicator=True)
        ex = ex[ex["_merge"] == "left_only"]
        print(f"  同花顺独有样例（前 5，即 004 缺的）:")
        print("   ", ex[["symbol", "date", "close_price"]].head(5).to_dict("records"))
        print(f"  同花顺独有 按股票数: {ex['symbol'].nunique()}")
    if only_004:
        ex2 = d.merge(t[["symbol", "date"]], on=["symbol", "date"], how="left", indicator=True)
        ex2 = ex2[ex2["_merge"] == "left_only"]
        print(f"  004 独有样例（前 5，即 同花顺缺的）:")
        print("   ", ex2[["symbol", "date", "close"]].head(5).to_dict("records"))

    if not len(m):
        sys.exit("❌ 无交集，无法对拍")

    # ── 逐值比对 ──
    print("\n" + "=" * 78)
    print("逐值比对（相对误差 = |a-b| / max(|a|,|b|,eps)）")
    print("=" * 78)
    # 注意：merge 后同名列会加后缀 _ths/_004，不同名列保持原名
    pairs = [("open_price", "open"), ("high_price", "high"),
             ("low_price", "low"), ("close_price", "close"),
             ("volume_ths", "volume_004"), ("turnover_ths", "turnover_004")]
    rep = {"window": [win[0], win[-1]], "n_codes": len(codes),
           "n_matched": len(m), "fields": {}}

    # 价格浮点允许 1e-4 相对误差；量额允许 1e-4（不同源四舍五入）
    TOL = {a: 1e-4 for a, _ in pairs}   # 价格与量额均允许 1e-4 相对误差（不同源四舍五入）
    for a, b in pairs:
        if a not in m.columns or b not in m.columns:
            print(f"  {a:12s} ⚠️ 列缺失"); continue
        x, y = m[a].astype(float), m[b].astype(float)
        denom = np.maximum(np.maximum(x.abs(), y.abs()), 1e-12)
        rel = (x - y).abs() / denom
        exact = (rel <= 1e-12).mean()
        within = (rel <= TOL[a]).mean()
        big = (rel > 0.01).mean()          # 差异 >1% 视为真不一致
        print(f"  {a:12s} vs {b:9s} 完全相等 {exact*100:6.2f}%  "
              f"误差<0.01% {within*100:6.2f}%  差异>1% {big*100:6.2f}%  "
              f"中位相对误差 {rel.median():.2e}")
        rep["fields"][a] = {"exact_pct": round(float(exact) * 100, 4),
                            "within_tol_pct": round(float(within) * 100, 4),
                            "gt1pct_pct": round(float(big) * 100, 4),
                            "median_rel_err": float(rel.median())}

    # ── 差异大的行细看（判断是否系统性口径差异） ──
    print("\n" + "=" * 78)
    print("差异 >1% 的行样例（判断是否口径差异，如复权/单位）")
    print("=" * 78)
    x, y = m["close_price"].astype(float), m["close"].astype(float)
    rel = (x - y).abs() / np.maximum(np.maximum(x.abs(), y.abs()), 1e-12)
    bad = m[rel > 0.01]
    print(f"close 差异>1% 的行数: {len(bad):,} / {len(m):,} "
          f"({len(bad)/len(m)*100:.3f}%)")
    if len(bad):
        print(f"涉及股票数: {bad['symbol'].nunique()}")
        show = bad.head(8).copy()
        show["rel_err"] = rel[bad.index[:8]].round(6)
        print(show[["symbol", "date", "close_price", "close", "rel_err"]]
              .rename(columns={"close_price": "同花顺", "close": "004"})
              .to_string(index=False))
        # 比率分布 → 若集中在某个常数倍，说明是复权口径差异
        ratio = (x / y)[rel > 0.01]
        print(f"\n  比值 同花顺/004 的分位: "
              f"p10={ratio.quantile(.1):.4f} p50={ratio.median():.4f} "
              f"p90={ratio.quantile(.9):.4f}")
        print("  （若比值集中在 1 附近而非固定倍数 → 是个股级数据差异，非系统性口径）")

    # volume 单位核对（同花顺文档说"股"，004 可能是"手"=100股）
    print("\n【单位核对】volume（同花顺文档标注单位=股；004 若为手则比值应为 100）")
    r = (m["volume_ths"].astype(float) / m["volume_004"].astype(float))
    print(f"  同花顺/004 比值 中位={r.median():.4f}  p25={r.quantile(.25):.4f} "
          f"p75={r.quantile(.75):.4f}")
    print(f"  比值≈1 占比 {(r.between(0.999,1.001)).mean()*100:.1f}%  "
          f"≈100 占比 {(r.between(99.9,100.1)).mean()*100:.1f}%")
    rep["volume_ratio_median"] = float(r.median())

    print("\n【单位核对】turnover（成交额）")
    r2 = (m["turnover_ths"].astype(float) / m["turnover_004"].astype(float))
    print(f"  同花顺/004 比值 中位={r2.median():.4f}  "
          f"比值≈1 占比 {(r2.between(0.999,1.001)).mean()*100:.1f}%")
    rep["turnover_ratio_median"] = float(r2.median())

    json.dump(rep, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(f"\n报告: {OUT}")


if __name__ == "__main__":
    main()

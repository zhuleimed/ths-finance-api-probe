#!/usr/bin/env python3
"""stock_daily_archive（退市股归档表）体检。

★ 限制先说清楚：**同花顺不覆盖退市股**，所以没有独立外部标尺，
   证明力天然弱于主表（主表有同花顺逐值对拍）。
   能做的是「内部自证」，分四层：

   L1 结构：行数 / 股票数 / 日期范围 / 主键唯一性
   L2 勾稽：high≥max(o,c) ≥ min(o,c) ≥ low 等恒等关系
   L3 物理约束：成交额÷成交量=均价，须落在当日 [low, high] 内
   L4 交叉：与主表 stock_daily 在同一 (股票,日期) 上的重叠值比对

用法：
  python scripts/audit_archive_table.py
"""
import os
import sqlite3

import numpy as np
import pandas as pd

DB = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main():
    conn = sqlite3.connect(DB)

    hr("L1 结构")
    d = pd.read_sql("SELECT * FROM stock_daily_archive", conn)
    print(f"   行数      : {len(d):,}")
    print(f"   列        : {list(d.columns)}")
    print(f"   股票数    : {d['symbol'].nunique():,}")
    print(f"   日期范围  : {d['date'].min()} ~ {d['date'].max()}")
    print(f"   交易日数  : {d['date'].nunique():,}")
    dup = int(d.duplicated(subset=["symbol", "date"]).sum())
    print(f"   主键重复  : {dup}  {'✅' if dup == 0 else '❌'}")

    hr("L2 OHLC 勾稽")
    v = d.dropna(subset=["open", "high", "low", "close"])
    checks = {
        "high >= low": v["high"] >= v["low"],
        "high >= open": v["high"] >= v["open"] - 1e-6,
        "high >= close": v["high"] >= v["close"] - 1e-6,
        "low <= open": v["low"] <= v["open"] + 1e-6,
        "low <= close": v["low"] <= v["close"] + 1e-6,
        "close > 0": v["close"] > 0,
        "volume >= 0": v["volume"] >= 0,
    }
    bad_any = pd.Series(False, index=v.index)
    for k, ok in checks.items():
        n = int((~ok).sum())
        bad_any |= (~ok)
        print(f"   {'✅' if n == 0 else '❌'} {k:16s} 违例 {n:>7,} / {len(v):,}")
    print(f"\n   合计违例行: {int(bad_any.sum()):,}")

    hr("L3 物理约束：成交额 ÷ 成交量 = 均价，须落在当日 [low, high]")
    if v["amount"].notna().any() and v["volume"].notna().any():
        vw = v["amount"].astype(float) / v["volume"].astype(float).replace(0, np.nan)
        inside = (vw >= v["low"] * 0.995) & (vw <= v["high"] * 1.005)
        nz = vw.notna() & (v["volume"] > 0)
        rate = inside[nz].mean() * 100 if nz.sum() else float("nan")
        print(f"   可检验 {int(nz.sum()):,} 行，均价在区间内 {rate:.3f}%")
        print(f"   {'✅ 通过' if rate > 99.9 else '⚠️ 有越界，需细查'}")
        out = v[nz & ~inside]
        if len(out):
            print(f"   越界 {len(out):,} 行，样例：")
            print(out.head(5)[["symbol", "date", "low", "high", "volume", "amount"]]
                  .to_string(index=False))
    else:
        print("   ⚠️ amount 或 volume 大量缺失，无法检验")

    hr("L4 交叉验证：与主表 stock_daily 的重叠 (股票,日期) 比对")
    main = pd.read_sql("SELECT symbol,date,open,high,low,close,volume,amount "
                       "FROM stock_daily", conn)
    m = d.merge(main, on=["symbol", "date"], suffixes=("_arc", "_main"))
    print(f"   重叠行数: {len(m):,}  （占归档表 {len(m)/len(d)*100:.1f}%）")
    if len(m):
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            a, b = m[f"{c}_arc"].astype(float), m[f"{c}_main"].astype(float)
            den = np.maximum(np.maximum(a.abs(), b.abs()), 1e-12)
            rel = (a - b).abs() / den
            n = int((rel > 1e-4).sum())
            print(f"     {c:8s} 不符>0.01%: {n:>7,}  {'✅' if n == 0 else '❌'}")
    else:
        print("     → 无重叠，无法交叉验证（退市股已被移出主表）")

    hr("L5 归档表独有部分的自检（无外部标尺）")
    only = d.merge(main[["symbol", "date"]], on=["symbol", "date"], how="left",
                   indicator=True)
    only = d[only["_merge"] == "left_only"]
    print(f"   归档独有 {len(only):,} 行 / {only['symbol'].nunique():,} 只")
    if len(only):
        v2 = only.dropna(subset=["close"])
        print(f"   其中 close 非空 {len(v2):,} 行")
        # 极端跳变（未经复权校正的退市股，除权会产生跳空，属预期）
        dd = v2.sort_values(["symbol", "date"]).copy()
        dd["prev"] = dd.groupby("symbol")["close"].shift(1)
        dd["chg"] = dd["close"] / dd["prev"] - 1
        ext = int((dd["chg"].abs() > 0.21).sum())
        print(f"   单日涨跌幅 |>21%| 的行: {ext:,} / {dd['chg'].notna().sum():,} "
              f"（除权跳空属预期，仅供参考）")
        # 陈旧数据特征
        g = dd.groupby("symbol")
        for c in ["open", "high", "low", "close", "volume"]:
            dd[f"s_{c}"] = g[c].shift().eq(dd[c])
        dd["same"] = dd[[f"s_{c}" for c in ["open", "high", "low", "close", "volume"]]].all(axis=1)
        dd["run"] = dd.groupby("symbol")["same"].transform(
            lambda s: s.groupby((~s).cumsum()).cumsum())
        stale = dd[dd["run"] >= 2]
        print(f"   连续≥3日 OHLCV 完全相同（疑似填充）: {len(stale):,} 行 / "
              f"{stale['symbol'].nunique() if len(stale) else 0} 只")

    hr("结论")
    print("   归档表无独立外部标尺（同花顺不覆盖退市股），只能做内部自证。")
    print("   上面各层的结果决定可信度；若 L2/L3 全过且 L4 无差异，可认为结构可信。")
    conn.close()


if __name__ == "__main__":
    main()

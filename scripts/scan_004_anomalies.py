#!/usr/bin/env python3
"""用同花顺全市场 dump 作为「独立第三方标尺」，全量扫描 004 行情库的数据异常。

背景：004_sequoia-x 的 stock_daily 来自腾讯/新浪/baostock；同花顺是完全独立的
第三个数据面。两者逐值比对，即可发现**单源自身无法察觉**的错误（如整行错位、
陈旧数据、缺失）。

扫描项：
  1. 逐日不一致股票数（差异 >1%）→ 定位污染日期
  2. 覆盖差异（一方有另一方无）→ 定位缺失/多填
  3. 「陈旧数据」特征：连续多日 OHLCV 完全相同（疑似停牌被填充）

用法：
  python scripts/scan_004_anomalies.py [--start 2026-01-01]
"""
import argparse
import os
import sqlite3

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    args = ap.parse_args()

    print("读取同花顺 dump …")
    t = pd.read_parquet(os.path.join(ROOT, "data/dump/daily-k.parquet"),
                        columns=["thscode", "date_ms", "open_price", "high_price",
                                 "low_price", "close_price", "volume", "turnover"])
    t["symbol"] = t["thscode"].str.split(".").str[0]
    t["date"] = (pd.to_datetime(t["date_ms"], unit="ms", utc=True)
                 .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    t = t.drop(columns=["thscode", "date_ms"])

    conn = sqlite3.connect(DB)
    d = pd.read_sql("SELECT symbol,date,open,high,low,close,volume,amount "
                    "FROM stock_daily WHERE date>=?", conn, params=(args.start,))
    conn.close()
    print(f"004 侧 {len(d):,} 行 / 同花顺侧 {len(t):,} 行（窗口 {args.start} 起）")

    m = t.merge(d, on=["symbol", "date"], how="inner", suffixes=("_t", "_d"))
    print(f"交集 {len(m):,} 行\n")

    # ── 1. 逐日不一致 ──
    print("=" * 78)
    print("扫描 1：逐日 close 不一致股票数（阈值 >1%）")
    print("=" * 78)
    m["bad"] = ((m["close_price"] - m["close"]).abs()
                / np.maximum(m["close"].abs(), 1e-9) > 0.01)
    per_day = m.groupby("date").agg(n=("bad", "size"), bad=("bad", "sum"))
    per_day["rate"] = per_day["bad"] / per_day["n"]
    clean = (per_day["bad"] == 0).sum()
    print(f"  完全一致交易日: {clean} / {len(per_day)}")
    dirty = per_day[per_day["bad"] > 0].sort_values("bad", ascending=False)
    print(f"  存在不一致的交易日: {len(dirty)} 天")
    if len(dirty):
        print(f"\n  Top 15 污染日：")
        print(f"  {'日期':<12} {'不一致':>7} {'总数':>7} {'占比':>8}")
        for dt, r in dirty.head(15).iterrows():
            print(f"  {dt:<12} {r['bad']:>7.0f} {r['n']:>7.0f} {r['rate']*100:>7.3f}%")

    # ── 2. 覆盖差异 ──
    print("\n" + "=" * 78)
    print("扫描 2：覆盖差异（一方有、另一方无）")
    print("=" * 78)
    only_t = len(t) - len(m)
    only_d = len(d) - len(m)
    print(f"  同花顺有 004 无 : {only_t:,} 行")
    print(f"  004 有 同花顺无 : {only_d:,} 行")

    key_d = set(zip(d["symbol"], d["date"]))
    key_t = set(zip(t["symbol"], t["date"]))
    od = pd.DataFrame(sorted(key_d - key_t), columns=["symbol", "date"])
    ot = pd.DataFrame(sorted(key_t - key_d), columns=["symbol", "date"])
    if len(od):
        print(f"\n  004 独有 按日统计 Top 10：")
        print(od.groupby("date").size().nlargest(10).to_string())
        print(f"  004 独有 按股票数: {od['symbol'].nunique()}")
    if len(ot):
        print(f"\n  同花顺独有 按日统计 Top 10：")
        print(ot.groupby("date").size().nlargest(10).to_string())

    # ── 3. 陈旧数据（连续完全相同 = 疑似停牌填充） ──
    print("\n" + "=" * 78)
    print("扫描 3：004 侧「陈旧数据」——连续 ≥3 日 OHLCV 完全相同")
    print("=" * 78)
    dd = d.sort_values(["symbol", "date"]).copy()
    dd["same"] = (dd.groupby("symbol")[["open", "high", "low", "close", "volume"]]
                  .apply(lambda g: g.eq(g.shift()).all(axis=1)).reset_index(level=0, drop=True)
                  if False else None)
    # 更稳的写法：逐列比较
    dd = dd.sort_values(["symbol", "date"])
    g = dd.groupby("symbol")
    for c in ["open", "high", "low", "close", "volume"]:
        dd[f"s_{c}"] = g[c].shift().eq(dd[c])
    dd["all_same"] = dd[[f"s_{c}" for c in ["open", "high", "low", "close", "volume"]]].all(axis=1)
    # 找连续 run：按 symbol 分组累计连续 all_same 长度
    dd["run"] = dd.groupby("symbol")["all_same"].transform(
        lambda s: s.groupby((~s).cumsum()).cumsum())
    stale = dd[dd["run"] >= 2]
    print(f"  连续 ≥3 日（含首日）完全相同的行数: {len(stale):,}")
    if len(stale):
        print(f"  涉及股票数: {stale['symbol'].nunique()}")
        ex = stale.groupby("symbol").size().nlargest(8)
        print(f"  最严重的 8 只（陈旧天数）:\n{ex.to_string()}")
        print(f"\n  样例：")
        s0 = stale["symbol"].iloc[0]
        print(dd[dd["symbol"] == s0].tail(12)[
            ["symbol", "date", "open", "high", "low", "close", "volume", "run"]]
            .to_string(index=False))

    # ── 4. 针对扫描1发现的污染日，逐只导出以确认错位 ──
    if len(dirty):
        worst = dirty.index[0]
        print("\n" + "=" * 78)
        print(f"扫描 4：污染最重日期 {worst} 的逐只明细（确认是否整行错位）")
        print("=" * 78)
        sub = m[m["date"] == worst]
        bad = sub[sub["bad"]].sort_values("symbol")
        print(f"  不一致 {len(bad)} 只，列前 15：")
        print(bad[["symbol", "close_price", "close"]].head(15)
              .rename(columns={"close_price": "同花顺", "close": "004"})
              .to_string(index=False))


if __name__ == "__main__":
    main()

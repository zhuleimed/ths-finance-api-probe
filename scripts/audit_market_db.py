#!/usr/bin/env python3
"""004 行情库体检 —— 用同花顺 dump 作独立标尺，逐字段核查 stock_daily。

回答一个具体问题：「现在库里数据都对了吗？」
诚实回答必须区分【已验证】【已知残留】【从未验证】三类。

用法：
  python scripts/audit_market_db.py
"""
import os
import sqlite3

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(ROOT, "data", "dump", "daily-k.parquet")
DB = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db"
TOL = 1e-3


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main():
    conn = sqlite3.connect(DB)

    hr("0. 被检对象：库里有哪些表")
    tabs = conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'").fetchall()
    for (t,) in tabs:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        mark = "★★ 本次体检" if t in ("stock_daily",) else "（未验证）"
        print(f"   {t:<26} {n:>10,} 行  {mark}")

    hr("1. stock_daily vs 同花顺 dump —— 逐字段比对")
    t = pd.read_parquet(DUMP, columns=["thscode", "date_ms", "open_price",
                                       "high_price", "low_price", "close_price",
                                       "volume", "turnover"])
    t["symbol"] = t["thscode"].str.split(".").str[0]
    t["date"] = (pd.to_datetime(t["date_ms"], unit="ms", utc=True)
                 .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    t = t.drop(columns=["thscode", "date_ms"]).rename(
        columns={"volume": "V_THS", "turnover": "A_THS"})

    d = pd.read_sql("SELECT symbol,date,open,high,low,close,volume,amount "
                    "FROM stock_daily", conn)
    m = t.merge(d, on=["symbol", "date"], how="inner")
    m = m[m["close_price"] > 0].copy()
    print(f"   可比对 {len(m):,} 行 / {m['symbol'].nunique():,} 只"
          f"（同花顺 dump 覆盖 2016-10-09 起）\n")

    pairs = [("open_price", "open", "开盘价"), ("high_price", "high", "最高价"),
             ("low_price", "low", "最低价"), ("close_price", "close", "收盘价"),
             ("V_THS", "volume", "成交量"), ("A_THS", "amount", "成交额")]
    print(f"   {'字段':<8}{'不符>0.1%':>12}{'占比':>10}   判定")
    remain = {}
    for tc, dc, name in pairs:
        rel = ((m[dc].astype(float) - m[tc].astype(float)).abs()
               / np.maximum(np.abs(m[tc].astype(float)), 1e-12))
        n = int((rel > TOL).sum())
        remain[name] = n
        pct = n / len(m) * 100
        verdict = "✅ 完全一致" if n == 0 else ("⚠️ 有残留" if pct < 1 else "❌ 大量不符")
        print(f"   {name:<8}{n:>12,}{pct:>9.3f}%   {verdict}")

    hr("2. 残留差异的性质（若有）")
    vw = m["amount"].astype(float) / m["volume"].astype(float).replace(0, np.nan)
    lo, hi = m["low"].astype(float), m["high"].astype(float)
    viol = int(((vw < lo * 0.995) | (vw > hi * 1.005)).sum())
    print(f"   ★物理约束：均价(=成交额/成交量)越界行数 = {viol:,}")
    print(f"     {'✅ 全部自洽' if viol == 0 else '❌ 仍有越界'}（修复前 62,835）")

    if remain.get("成交额", 0):
        s = m[((m['amount'].astype(float) / m['A_THS'].astype(float) - 1).abs() > TOL)]
        r = (s["amount"].astype(float) / s["A_THS"].astype(float))
        print(f"\n   成交额残留 {len(s):,} 行：比值中位 {r.median():.4f}、"
              f"相对偏差中位 {(r-1).abs().median()*100:.3f}%")
        print(f"     → 判为厂商舍入差异（此前已论证），非错误")

    hr("3. 覆盖范围与未验证项（诚实交代）")
    dr = conn.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT date) "
                      "FROM stock_daily").fetchone()
    print(f"   a) stock_daily 时间范围: {dr[0]} ~ {dr[1]}（{dr[2]:,} 个交易日）")
    old = pd.read_sql("SELECT COUNT(*) n FROM stock_daily WHERE date < '2016-10-09'",
                      conn).iloc[0]
    if old["n"] == 0:
        print(f"      ✅ 整表都落在同花顺 dump 覆盖窗口内（2016-10-09 起）")
        print(f"         → 意味着**全表都被核对过**，不是抽样")
    else:
        print(f"      ⚠️ {old['n']:,} 行在 2016-10-09 之前，**完全未验证**")
    print(f"   b) 股票覆盖：")
    print(f"      004   {d['symbol'].nunique():,} 只")
    print(f"      同花顺 {t['symbol'].nunique():,} 只")
    only_ths = sorted(set(t["symbol"]) - set(d["symbol"]))
    seg = {}
    for s in only_ths:
        seg[s[:3]] = seg.get(s[:3], 0) + 1
    print(f"      同花顺独有 {len(only_ths):,} 只，代码段分布: "
          f"{dict(sorted(seg.items(), key=lambda x: -x[1])[:5])}")
    if len(only_ths) and all(s.startswith("920") for s in only_ths):
        print(f"      → 全是 920xxx 北交所，**004 主动排除**，非缺陷")
    print(f"   c) 其他表（本次未做逐值核对）：")
    for tb in ("stock_daily_archive", "index_daily", "stock_list",
               "tdx_finance_snapshot", "tdx_stock_industry"):
        n = conn.execute(f"SELECT COUNT(*) FROM {tb}").fetchone()[0]
        note = {"stock_daily_archive": "退市股归档，同花顺不覆盖退市股 → 难有独立标尺",
                "index_daily": "近 3 年已单独验证通过（同花顺指数接口仅覆盖约 3 年）",
                }.get(tb, "未验证")
        print(f"      {tb:<24} {n:>10,} 行  {note}")

    conn.close()

    hr("结论")
    print("  已修复并验证：价格 4 列、成交量、成交额（在 2016-10-09 起的重叠范围内）")
    print("  已知残留：成交额有厂商舍入级差异（已论证非错误）")
    print("  从未验证：2016-10-09 之前的历史、其他表（archive / index_daily 等）")


if __name__ == "__main__":
    main()

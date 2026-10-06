#!/usr/bin/env python3
"""Parquet 全市场导出的数据质量验证 —— 对标 004_sequoia-x 现有行情源。

验证四层：
  L1 结构完整性：行数 / 股票数 / 日期范围 / 主键唯一性
  L2 字段质量：缺失率 / 零值率 / 异常值
  L3 业务一致性：OHLC 勾稽关系（high>=max(o,c) >= min(o,c) >= low 等）
  L4 ★跨源对拍：抽 N 只股票，与 004 现有腾讯/新浪行情逐日比对 OHLCV

用法：
  python scripts/verify_dump_quality.py [--sample 30]
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(ROOT, "data", "dump")
OUT = os.path.join(ROOT, "data", "dump_quality_report.json")


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=30, help="跨源对拍的股票数")
    args = ap.parse_args()

    rep = {}
    hr("L1 结构完整性 —— 十年全量日K")
    t0 = pd.Timestamp.now()
    dk = pd.read_parquet(os.path.join(DUMP, "daily-k.parquet"))
    print(f"读取耗时 {(pd.Timestamp.now()-t0).total_seconds():.1f}s")
    print(f"行数        : {len(dk):,}")
    print(f"列          : {list(dk.columns)}")
    print(f"股票数      : {dk['thscode'].nunique():,}")
    dk["date"] = pd.to_datetime(dk["date_ms"], unit="ms")
    print(f"日期范围    : {dk['date'].min().date()} ~ {dk['date'].max().date()}")
    print(f"交易日数    : {dk['date'].nunique():,}")
    print(f"内存占用    : {dk.memory_usage(deep=True).sum()/1024**2:.0f} MB")
    rep["daily_k"] = {
        "rows": len(dk), "stocks": int(dk["thscode"].nunique()),
        "date_min": str(dk["date"].min().date()), "date_max": str(dk["date"].max().date()),
        "trading_days": int(dk["date"].nunique()),
    }

    # 主键唯一性
    dup = dk.duplicated(subset=["thscode", "date_ms"]).sum()
    print(f"\n主键 (thscode,date_ms) 重复行: {dup:,}  "
          f"{'✅ 唯一' if dup == 0 else '❌ 存在重复'}")
    rep["daily_k"]["duplicate_keys"] = int(dup)

    # 每日股票数分布（看是否有整段缺失）
    per_day = dk.groupby("date")["thscode"].nunique()
    print(f"\n每日股票数：中位 {per_day.median():.0f}  最小 {per_day.min()}  "
          f"最大 {per_day.max()}")
    print(f"  每日股票数 <1000 的交易日: {(per_day < 1000).sum()} 天")
    worst = per_day.nsmallest(5)
    print("  最少的 5 天：")
    for d, n in worst.items():
        print(f"    {d.date()}  {n} 只")
    rep["daily_k"]["per_day_median"] = float(per_day.median())
    rep["daily_k"]["per_day_min"] = int(per_day.min())

    hr("L2 字段质量 —— 缺失率 / 异常值")
    cols = ["open_price", "high_price", "low_price", "close_price", "volume", "turnover"]
    q = {}
    for c in cols:
        if c not in dk.columns:
            print(f"  {c}: ❌ 列不存在"); continue
        s = dk[c]
        null_r = s.isna().mean()
        zero_r = (s == 0).mean()
        neg_r = (s < 0).mean()
        q[c] = {"null_rate": round(float(null_r), 6), "zero_rate": round(float(zero_r), 6),
                "neg_rate": round(float(neg_r), 6), "min": float(s.min()), "max": float(s.max())}
        print(f"  {c:14s} 缺失 {null_r*100:6.3f}%  零值 {zero_r*100:7.3f}%  "
              f"负值 {neg_r*100:6.3f}%  范围[{s.min():.2f}, {s.max():.2f}]")
    rep["daily_k"]["field_quality"] = q

    hr("L3 业务一致性 —— OHLC 勾稽关系")
    v = dk.dropna(subset=["open_price", "high_price", "low_price", "close_price"])
    n = len(v)
    checks = {
        "high >= low": (v["high_price"] >= v["low_price"]),
        "high >= open": (v["high_price"] >= v["open_price"] - 1e-6),
        "high >= close": (v["high_price"] >= v["close_price"] - 1e-6),
        "low <= open": (v["low_price"] <= v["open_price"] + 1e-6),
        "low <= close": (v["low_price"] <= v["close_price"] + 1e-6),
        "volume >= 0": (v["volume"] >= 0),
        "turnover >= 0": (v["turnover"] >= 0),
    }
    inconsistent = pd.Series(False, index=v.index)
    for name, ok in checks.items():
        bad = (~ok).sum()
        inconsistent |= (~ok)
        flag = "✅" if bad == 0 else "❌"
        print(f"  {flag} {name:16s} 违例 {bad:>7,} / {n:,}  ({bad/n*100:.4f}%)")
    rep["daily_k"]["ohlc_violations"] = int(inconsistent.sum())
    if inconsistent.sum():
        ex = v[inconsistent].head(3)[["thscode", "date", "open_price", "high_price",
                                      "low_price", "close_price", "volume"]]
        print("\n  违例行样例：")
        print(ex.to_string(index=False))

    hr("L3b 极端价格跳变检测（疑似除权未复权 / 数据错误）")
    d = v.sort_values(["thscode", "date_ms"]).copy()
    d["prev_close"] = d.groupby("thscode")["close_price"].shift(1)
    d["chg"] = d["close_price"] / d["prev_close"] - 1
    # A股涨跌停 ±10%（ST 5%，科创/创业 20%），留余量取 ±21% 为阈值
    extreme = d[d["chg"].abs() > 0.21]
    print(f"  单日涨跌幅 |>21%| 的行数: {len(extreme):,} / {len(d):,} "
          f"({len(extreme)/len(d)*100:.4f}%)")
    if len(extreme):
        print(f"  涉及股票数: {extreme['thscode'].nunique()}")
        print("\n  样例（前 5）：")
        print(extreme.head(5)[["thscode", "date", "prev_close", "close_price", "chg"]]
              .to_string(index=False))
    rep["daily_k"]["extreme_jumps"] = int(len(extreme))
    rep["daily_k"]["extreme_jump_stocks"] = int(extreme["thscode"].nunique())

    # ── 与 004 现有行情对拍 ──
    hr("L4 ★跨源对拍 —— 同花顺 vs 004 现有行情源")
    db = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db"
    if not os.path.exists(db):
        print(f"  ⚠️ 未找到 004 数据库 {db}，跳过跨源对拍")
    else:
        import sqlite3
        conn = sqlite3.connect(db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print(f"  004 数据库表: {tables[:12]}")
        # 找日线表
        tbl = "stock_daily" if "stock_daily" in tables else None
        if tbl is None:
            print("  ⚠️ 无 stock_daily 表，跳过")
        else:
            cols_db = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
            print(f"  {tbl} 列: {cols_db}")
            # 取最近 60 个交易日做对拍
            recent = sorted(dk["date"].dt.strftime("%Y-%m-%d").unique())[-60:]
            print(f"  对拍窗口: {recent[0]} ~ {recent[-1]} ({len(recent)} 个交易日)")

            codes = conn.execute(
                f"SELECT DISTINCT symbol FROM {tbl} LIMIT ?", (args.sample,)).fetchall()
            codes = [c[0] for c in codes]
            print(f"  抽样股票 {len(codes)} 只: {codes[:8]}")

            rows = []
            for code in codes:
                for suf in (".SH", ".SZ"):
                    sub = dk[(dk["thscode"] == code + suf)]
                    if len(sub):
                        break
                if not len(sub):
                    continue
                sub = sub[sub["date"].dt.strftime("%Y-%m-%d").isin(recent)]
                got = pd.read_sql(
                    f"SELECT * FROM {tbl} WHERE symbol=? AND date>=? AND date<=?",
                    conn, params=(code, recent[0], recent[-1]))
                if not len(got):
                    continue
                m = sub.merge(got, left_on=sub["date"].dt.strftime("%Y-%m-%d"),
                              right_on="date", suffixes=("_ths", "_db"))
                if len(m):
                    rows.append((code, len(sub), len(got), len(m)))
            conn.close()

            if rows:
                cmp = pd.DataFrame(rows, columns=["code", "n_ths", "n_db", "n_matched"])
                print(f"\n  可比对样本: {len(cmp)} 只")
                print(f"  日均匹配行数: 同花顺 {cmp['n_ths'].mean():.0f}  "
                      f"004 {cmp['n_db'].mean():.0f}  匹配 {cmp['n_matched'].mean():.0f}")
                rep["cross_check"] = cmp.to_dict("records")
            else:
                print("  ⚠️ 未匹配到可比对数据（列名/日期格式可能不同，需人工确认）")

    json.dump(rep, open(OUT, "w"), ensure_ascii=False, indent=1, default=str)
    print(f"\n\n报告已存: {OUT}")


if __name__ == "__main__":
    main()

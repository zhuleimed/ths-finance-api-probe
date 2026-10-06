#!/usr/bin/env python3
"""修复 004 中「复权口径不符约定」的**价格列**（约定：004 应为不复权）。

━━ 背景（2026-10-06 实测）━━
用同花顺不复权 dump 逐行比对 004 的 stock_daily：

  A. 复权段污染：2024-01-02 ~ 2026-06-08，约 4 万行的**价格列**被复权因子缩放
     （比值分段恒定，如 300203 为 1.0546/1.0682；OHLC 四列同比例 —— 99.91% 证实）
  B. 2026-08-11 单日错位：10 行（000002~000016），值与任何日期都对不上

━━ 为什么不修 volume/amount ━━
在**价格完全正确**的行上，volume 仍有 0.751%、amount 仍有 4.42% 与同花顺不符，
且偏差跨全部年份。这与复权 bug 无关，是**另一类问题**（疑单位/独立错误），
尚未判定哪个源更可信 ⇒ **本次不动，单独报告**。

━━ 修复原则 ━━
  1. 只改价格 4 列；量额原样保留
  2. 备份原始值 CSV + 整库副本
  3. 默认 dry-run，`--apply` 才写库
  4. 写后逐行验证

用法：
  python scripts/fix_adjusted_rows.py            # dry-run
  python scripts/fix_adjusted_rows.py --apply    # 执行
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(ROOT, "data", "dump", "daily-k.parquet")
SX = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x"
DB = os.path.join(SX, "data", "sequoia_v2.db")
BACKUP_DIR = os.path.join(ROOT, "data", "backup")

PRICE_MAP = {"open": "open_price", "high": "high_price",
             "low": "low_price", "close": "close_price"}
ALL_MAP = {**PRICE_MAP, "volume": "volume", "amount": "turnover"}


def load(conn):
    t = pd.read_parquet(DUMP, columns=["thscode", "date_ms", "open_price",
                                       "high_price", "low_price", "close_price",
                                       "volume", "turnover"])
    t["symbol"] = t["thscode"].str.split(".").str[0]
    t["date"] = (pd.to_datetime(t["date_ms"], unit="ms", utc=True)
                 .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    t = t.drop(columns=["thscode", "date_ms"])
    d = pd.read_sql("SELECT id,symbol,date,open,high,low,close,volume,amount "
                    "FROM stock_daily", conn)
    d = d.rename(columns={**{c: f"db_{c}" for c in ALL_MAP}, "id": "db_id"})
    m = t.merge(d, on=["symbol", "date"], how="inner")
    return m[m["close_price"] > 0].copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--tol", type=float, default=1e-3)
    args = ap.parse_args()

    print("=" * 78)
    print(f"004 复权价格修复 · 容差 {args.tol} · 模式="
          f"{'★APPLY 真实写入' if args.apply else 'dry-run 只读'}")
    print("=" * 78)

    conn = sqlite3.connect(DB)
    print("\n[1/4] 载入并比对 …")
    m = load(conn)
    print(f"     可比对 {len(m):,} 行 / {m['symbol'].nunique():,} 只")

    need = {}
    print("\n     各字段不符行数：")
    for dbcol, tcol in ALL_MAP.items():
        denom = np.maximum(np.abs(m[tcol].astype(float)), 1e-12)
        rel = (m[f"db_{dbcol}"].astype(float) - m[tcol].astype(float)).abs() / denom
        need[dbcol] = (rel > args.tol).values
        mark = "→ 修复" if dbcol in PRICE_MAP else "→ 本次不动（另类问题）"
        print(f"       {dbcol:<8} {need[dbcol].sum():>8,} 行 "
              f"({need[dbcol].mean()*100:6.3f}%)  {mark}")

    # 只取价格列
    bad_mask = np.zeros(len(m), dtype=bool)
    for c in PRICE_MAP:
        bad_mask |= need[c]
    bad = m[bad_mask].copy()
    print(f"\n     待修价格行: {len(bad):,} 行 / {bad['symbol'].nunique():,} 只")
    print(f"     日期范围: {bad['date'].min()} ~ {bad['date'].max()}")
    print(f"     按年分布: {bad['date'].str[:4].value_counts().sort_index().to_dict()}")
    if not len(bad):
        sys.exit("\n     ✅ 无需修复")

    # 边界确认
    print("\n[2/4] 边界确认")
    pre = int((bad["date"] < "2024-01-02").sum())
    print(f"     2024-01-02 之前的坏行: {pre} 行 "
          f"{'✅ 与「2024-01-02 起灌入」的结论一致' if pre == 0 else '⚠️ 超出预期'}")

    # 备份
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(BACKUP_DIR, f"stock_daily_price_{ts}.csv")
    bad[["db_id", "symbol", "date"] + [f"db_{c}" for c in PRICE_MAP]].to_csv(
        bak, index=False)
    print(f"\n[3/4] 备份 {len(bad):,} 行原始价格 → {bak}")
    if args.apply:
        db_bak = os.path.join(BACKUP_DIR, f"sequoia_v2_{ts}.db.bak")
        print(f"     复制整库 → {db_bak} …")
        shutil.copy2(DB, db_bak)
        print(f"     完成 {os.path.getsize(db_bak)/1024**3:.2f} GB")
    if not args.apply:
        print("\n[4/4] ⏸ dry-run，未写入。加 --apply 执行。")
        return

    # 写入
    print("\n[4/4] 写入价格列 …")
    sets = ",".join(f"{c}=?" for c in PRICE_MAP)
    sql = f"UPDATE stock_daily SET {sets} WHERE id=?"
    order = list(PRICE_MAP)   # 与 sets 的列顺序严格一致
    rows = [tuple(float(r[PRICE_MAP[c]]) for c in order) + (int(r["db_id"]),)
            for _, r in bad.iterrows()]
    conn.executemany(sql, rows)
    conn.commit()
    print(f"     已更新 {len(rows):,} 行")

    # 验证
    print("\n     验证 …")
    m2 = load(conn)
    conn.close()
    left_total = 0
    for dbcol, tcol in ALL_MAP.items():
        denom = np.maximum(np.abs(m2[tcol].astype(float)), 1e-12)
        rel = (m2[f"db_{dbcol}"].astype(float) - m2[tcol].astype(float)).abs() / denom
        c = int((rel > args.tol).sum())
        if dbcol in PRICE_MAP:
            left_total += c
        print(f"       {dbcol:<8} 仍不符 {c:>8,}"
              f"{'  ✅' if dbcol in PRICE_MAP and c == 0 else ''}")
    print(f"\n     价格列修复结果: {'✅ 全部归零' if left_total == 0 else f'⚠️ 仍有 {left_total} 处'}")


if __name__ == "__main__":
    main()

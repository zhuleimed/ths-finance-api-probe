#!/usr/bin/env python3
"""修复 004_sequoia-x 在 2026-07-06 的单日数据污染（129 行）。

背景（见 reports/同花顺接口能力实测报告_20261006.md §4.2a）：
    用同花顺全市场 Parquet dump 作独立标尺对拍，发现 004 的 stock_daily
    在 2026-07-06 有 129 行错误（92 只 002xxx + 37 只 300xxx），
    错值既不等于自身邻近日期的值、也不等于同花顺的值 → 判定为独立错误。

修复原则（安全第一）：
    1. 【前置检查】确认目标股票在 004 中是「不复权」口径（用户约定 004 就是不复权）
       —— 若发现是复权口径，则中止，避免把不复权价灌进复权序列造成新跳变
    2. 【备份】先导出原值到 CSV，可回滚
    3. 【写入】只在 --apply 时真正修改；默认 dry-run 只看不改
    4. 【验证】改完重新对拍，确认差异归零

用法：
    # 只看方案，不改数据（默认）
    python scripts/fix_20260706_corruption.py

    # 真正执行修复
    python scripts/fix_20260706_corruption.py --apply
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
TARGET_DATE = "2026-07-06"
THRESH = 0.01          # 相对误差 >1% 判为不一致
BACKUP_DIR = os.path.join(ROOT, "data", "backup")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入数据库（默认只 dry-run）")
    args = ap.parse_args()

    print("=" * 78)
    print(f"004 数据修复 · 目标日期 {TARGET_DATE} · 模式={'★APPLY 真实写入' if args.apply else 'dry-run 只读'}")
    print("=" * 78)

    # ── 1. 载入同花顺真值 ──
    print("\n[1/5] 载入同花顺 dump …")
    t = pd.read_parquet(DUMP, columns=["thscode", "date_ms", "open_price",
                                       "high_price", "low_price", "close_price",
                                       "volume", "turnover"])
    t["symbol"] = t["thscode"].str.split(".").str[0]
    t["date"] = (pd.to_datetime(t["date_ms"], unit="ms", utc=True)
                 .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    t = t.drop(columns=["thscode", "date_ms"])

    conn = sqlite3.connect(DB)
    # 004 的表主键列名是 id（不是 rowid），用它定位待更新行
    d = pd.read_sql("SELECT id,symbol,date,open,high,low,close,volume,amount "
                    "FROM stock_daily WHERE date=?", conn, params=(TARGET_DATE,))
    print(f"     同花顺侧 {len(t):,} 行 | 004 当日 {len(d):,} 行")

    # 显式重命名，避免 merge 后同名列加后缀导致的引用混乱
    d = d.rename(columns={"open": "db_open", "high": "db_high", "low": "db_low",
                          "close": "db_close", "volume": "db_volume",
                          "amount": "db_amount"})
    m = t[t["date"] == TARGET_DATE].merge(d, on=["symbol", "date"])
    m["rel"] = (m["close_price"] - m["db_close"]).abs() / m["db_close"].abs()
    bad = m[m["rel"] > THRESH].copy()
    print(f"\n     差异 >{THRESH*100:.0f}% 的行: {len(bad)} 行 / {len(m):,} 行")
    if not len(bad):
        sys.exit("     无需修复，退出。")

    # ── 2. 前置安全检查：这批股票在 004 里是不是「不复权」 ──
    print(f"\n[2/5] ★前置安全检查：确认目标股票在 004 中是「不复权」口径")
    d_all = pd.read_sql("SELECT symbol,date,close FROM stock_daily "
                        "WHERE date>=? AND date<=?",
                        conn, params=("2026-01-01", "2026-12-31"))
    j = t[(t["date"] >= "2026-01-01")].merge(d_all, on=["symbol", "date"]).rename(
        columns={"close": "db"})
    j["ratio"] = j["db"] / j["close_price"]
    g = j[j["symbol"].isin(set(bad["symbol"]))].groupby("symbol")["ratio"].agg(
        ["median", "std", "size"])
    g = g[g["size"] >= 50]
    is_raw = g[np.isclose(g["median"], 1.0, rtol=1e-3)]
    not_raw = g[~np.isclose(g["median"], 1.0, rtol=1e-3)]
    print(f"     目标 {len(set(bad['symbol']))} 只中：")
    print(f"       ✅ 不复权口径（与同花顺一致）: {len(is_raw)} 只")
    print(f"       ❌ 复权口径（比值≠1，需人工判断）: {len(not_raw)} 只")
    if len(not_raw):
        print(f"\n     复权口径股票明细（前 20）：")
        print(not_raw.head(20).to_string())
        print("\n     ⚠️ 中止：这些股票在 004 中不是不复权，直接灌入不复权价会制造新跳变。")
        print("        请先与用户确认这些股票的正确口径，再单独处理。")
        bad = bad[bad["symbol"].isin(set(is_raw.index))]
        print(f"        → 本次仅处理其中的 {len(bad)} 行不复权股票")

    if not len(bad):
        sys.exit("     无可安全修复的行，退出。")

    # ── 3. 展示修复方案 ──
    print(f"\n[3/5] 修复方案：{len(bad)} 行，涉及 {bad['symbol'].nunique()} 只股票")
    print(f"     代码段分布: {bad['symbol'].str[:3].value_counts().to_dict()}")
    print(f"\n     前 10 行明细（原值 → 新值）：")
    print(f"     {'代码':<9}{'原 close':>11}{'新 close':>11}{'相对误差':>10}")
    for _, r in bad.head(10).iterrows():
        print(f"     {r['symbol']:<9}{r['db_close']:>11.2f}{r['close_price']:>11.2f}{r['rel']*100:>9.2f}%")

    # ── 4. 备份（导出 004 侧原始值，可回滚） ──
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(BACKUP_DIR, f"stock_daily_{TARGET_DATE.replace('-','')}_{ts}.csv")
    bad[["id", "symbol", "date", "db_open", "db_high", "db_low",
         "db_close", "db_volume", "db_amount"]].to_csv(bak, index=False)
    print(f"\n[4/5] 备份原始行 → {bak}")

    db_bak = os.path.join(BACKUP_DIR, f"sequoia_v2_{ts}.db.bak")
    if args.apply:
        print(f"     复制整库备份 → {db_bak}（1.8GB，稍候…）")
        shutil.copy2(DB, db_bak)
        print(f"     库备份完成 {os.path.getsize(db_bak)/1024**3:.2f} GB")

    # ── 5. 写入 + 验证 ──
    if not args.apply:
        print(f"\n[5/5] ⏸ dry-run 模式，未写入。加 --apply 执行修复。")
        return

    print(f"\n[5/5] 写入数据库 …")
    n = 0
    for _, r in bad.iterrows():
        conn.execute(
            "UPDATE stock_daily SET open=?,high=?,low=?,close=?,volume=?,amount=? "
            "WHERE id=?",
            (float(r["open_price"]), float(r["high_price"]), float(r["low_price"]),
             float(r["close_price"]), float(r["volume"]), float(r["turnover"]),
             int(r["id"])))
        n += 1
    conn.commit()
    print(f"     已更新 {n} 行")

    # 验证：重新对拍
    d2 = pd.read_sql("SELECT symbol,date,close FROM stock_daily WHERE date=?",
                     conn, params=(TARGET_DATE,))
    d2 = d2.rename(columns={"close": "db_close"})
    m2 = t[t["date"] == TARGET_DATE].merge(d2, on=["symbol", "date"])
    m2["rel"] = (m2["close_price"] - m2["db_close"]).abs() / m2["db_close"].abs()
    left = (m2["rel"] > THRESH).sum()
    print(f"\n     ✅ 验证：修复后差异 >1% 的行 = {left}（修复前 {len(bad)}）")
    if left:
        print(m2[m2["rel"] > THRESH][["symbol", "close_price", "db_close", "rel"]].head(10)
              .to_string(index=False))
    conn.close()


if __name__ == "__main__":
    main()

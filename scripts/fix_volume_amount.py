#!/usr/bin/env python3
"""修复 004 的 volume / amount 错误行（A 组：68,781 行）。

━━ 怎么发现是错的（不靠外部数据，靠物理约束自证）━━
规律：**成交额 ÷ 成交量 = 当日均价**，均价必须落在当天的 [最低价, 最高价] 之间。

    同花顺：100.000% 满足  ✅
    004   ： 99.170% 满足  ❌ 62,835 行违反

再用两项独立检验确认同花顺是对的：
    1. 隔日成交量跳变 >10 倍：004 有 5.45%，同花顺仅 0.06%
    2. 004 错行的 amount 比值中位 = 0.0001（1/10000，手×万元的双重缩放）

━━ 修复范围（68,781 行）━━
    A1 均价越界              62,835 行   ← 可证错误
    A2 volume 与同花顺不符     5,946 行   ← 同一个手/股 bug，量额同缩放侥幸未被 A1 捕获
    union                    68,781 行 / 3,203 只中受影响的那部分

━━ 刻意不修的部分（236,860 行，仅 amount 小差异）━━
    差异中位 0.76%、按股票随机波动（std 0.015）、均价正常（1.0153）
    → 判定为厂商舍入差异，非错误。改它等于用另一家的舍入覆盖本家。
    详见 reports/同花顺接口能力实测报告_20261006.md §4.2d

用法：
  python scripts/fix_volume_amount.py             # dry-run
  python scripts/fix_volume_amount.py --apply     # 执行
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
TOL = 1e-3
# 均价容差：允许 0.5% 边界余量（不同源的均价口径略有差异）
VWAP_TOL = 0.005


def load(conn):
    t = pd.read_parquet(DUMP, columns=["thscode", "date_ms", "high_price",
                                       "low_price", "close_price", "volume", "turnover"])
    t["symbol"] = t["thscode"].str.split(".").str[0]
    t["date"] = (pd.to_datetime(t["date_ms"], unit="ms", utc=True)
                 .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    t = t.drop(columns=["thscode", "date_ms"]).rename(
        columns={"volume": "V_THS", "turnover": "A_THS"})
    d = pd.read_sql("SELECT id,symbol,date,high,low,volume,amount FROM stock_daily", conn)
    d = d.rename(columns={"id": "rid", "volume": "V_004", "amount": "A_004"})
    m = t.merge(d, on=["symbol", "date"], how="inner")
    return m[(m["close_price"] > 0)].copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    print("=" * 78)
    print(f"004 volume/amount 修复 · 模式={'★APPLY 真实写入' if args.apply else 'dry-run 只读'}")
    print("=" * 78)

    conn = sqlite3.connect(DB)
    print("\n[1/5] 载入比对 …")
    m = load(conn)
    print(f"     可比对 {len(m):,} 行")

    # ── 两个判据 ──
    vwap = m["A_004"].astype(float) / m["V_004"].astype(float).replace(0, np.nan)
    m["vwap_bad"] = ((vwap < m["low"] * (1 - VWAP_TOL)) |
                     (vwap > m["high"] * (1 + VWAP_TOL))).fillna(False)
    m["vol_diff"] = ((m["V_004"].astype(float) / m["V_THS"].astype(float) - 1).abs()
                     > TOL).fillna(False)
    m["amt_diff"] = ((m["A_004"].astype(float) / m["A_THS"].astype(float) - 1).abs()
                     > TOL).fillna(False)

    need = m["vwap_bad"] | m["vol_diff"]
    extra = m["amt_diff"] & ~need
    bad = m[need].copy()

    print(f"\n[2/5] 需修复的行")
    print(f"     均价越界                : {int(m['vwap_bad'].sum()):>8,}")
    print(f"     volume 与同花顺不符     : {int(m['vol_diff'].sum()):>8,}")
    print(f"     ── 并集（本次修复）     : {len(bad):>8,} 行")
    print(f"     仅 amount 小差异（不修）: {int(extra.sum()):>8,} 行  "
          f"→ 厂商舍入，均价正常，刻意不动")
    print(f"\n     涉及股票 {bad['symbol'].nunique():,} 只")
    print(f"     年份分布: {bad['date'].str[:4].value_counts().sort_index().to_dict()}")

    if not len(bad):
        sys.exit("     ✅ 无需修复")

    # ── 验证修复方案 ──
    lo = bad["low"].astype(float)
    hi = bad["high"].astype(float)
    v2 = bad["A_THS"].astype(float) / bad["V_THS"].astype(float).replace(0, np.nan)
    ok = (v2 >= lo * (1 - VWAP_TOL)) & (v2 <= hi * (1 + VWAP_TOL))
    print(f"\n[3/5] 方案验证：替换为同花顺值后，均价落回区间 = {ok.mean()*100:.3f}%")
    if ok.mean() < 0.999:
        print("     ⚠️ 未达 99.9%，中止以免误改")
        return

    # ── 备份 ──
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(BACKUP_DIR, f"stock_daily_volamt_{ts}.csv")
    bad[["rid", "symbol", "date", "V_004", "A_004"]].to_csv(bak, index=False)
    print(f"\n[4/5] 备份 {len(bad):,} 行原值 → {bak}")
    if args.apply:
        db_bak = os.path.join(BACKUP_DIR, f"sequoia_v2_{ts}.db.bak")
        print(f"     复制整库 → {db_bak} …")
        shutil.copy2(DB, db_bak)
        print(f"     完成 {os.path.getsize(db_bak)/1024**3:.2f} GB")
    if not args.apply:
        print("\n[5/5] ⏸ dry-run，未写入。加 --apply 执行。")
        return

    # ── 写入 ──
    print("\n[5/5] 写入 …")
    rows = [(float(r["V_THS"]), float(r["A_THS"]), int(r["rid"]))
            for _, r in bad.iterrows()]
    conn.executemany("UPDATE stock_daily SET volume=?,amount=? WHERE id=?", rows)
    conn.commit()
    print(f"     已更新 {len(rows):,} 行")

    # ── 验证 ──
    m2 = load(conn)
    conn.close()
    vw = m2["A_004"].astype(float) / m2["V_004"].astype(float).replace(0, np.nan)
    viol = ((vw < m2["low"] * (1 - VWAP_TOL)) | (vw > m2["high"] * (1 + VWAP_TOL)))
    print(f"\n     验证① 均价越界行: {int(viol.sum()):,}（修复前 62,835）")
    vd = int(((m2["V_004"].astype(float) / m2["V_THS"].astype(float) - 1).abs() > TOL).sum())
    print(f"     验证② volume 不符: {vd:,}（修复前 56,635）")
    print(f"     {'✅ 两项均归零' if int(viol.sum()) == 0 and vd == 0 else '⚠️ 仍有残留'}")


if __name__ == "__main__":
    main()

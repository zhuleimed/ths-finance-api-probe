#!/usr/bin/env python3
"""★同步代码体检：直接调用 004 的数据源函数，把输出与同花顺真值对拍。

目的：回答「10/8 交易恢复后，同步会不会又写入错误数据」。

不满足于"读代码+看注释"——那正是当年漏掉 bug 的方式。本脚本**真的去调**：
  1. TencentSource.get_daily  → volume 是否已转成「股」
  2. SinaSource.get_daily     → volume 单位（新浪原生是股？）
  3. TencentSource.get_realtime → amount 是否已转成「元」
  4. 复权口径：各源在**除权日附近**的值是否 = 不复权实际价

判据（与同花顺不复权 dump 对拍）：价格一致 → 不复权；volume 一致 → 股；amount 一致 → 元。

用法：
  python scripts/audit_sync_sources.py
"""
import os
import sys

import pandas as pd

SX = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x"
sys.path.insert(0, SX)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(ROOT, "data", "dump", "daily-k.parquet")

# 测试标的：含 2026 年有分红送转（除权）的票，专门验复权口径
CASES = ["sh600519", "sz000001", "sz300203", "sz000002"]


def load_ths():
    t = pd.read_parquet(DUMP, columns=["thscode", "date_ms", "close_price",
                                       "volume", "turnover"])
    t["symbol"] = t["thscode"].str.split(".").str[0]
    t["date"] = (pd.to_datetime(t["date_ms"], unit="ms", utc=True)
                 .dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))
    return t.drop(columns=["thscode", "date_ms"])


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main():
    from sequoia_x.data.tencent_source import TencentSource, SinaSource

    ths = load_ths()
    tc, sn = TencentSource(), SinaSource()

    for code in CASES:
        sym = code[2:]
        hr(f"{code}（{sym}）")

        for name, src in (("腾讯 TencentSource", tc), ("新浪 SinaSource", sn)):
            print(f"\n--- {name} ---")
            try:
                df = src.get_daily(code, days=40)
            except Exception as e:
                print(f"   ❌ 调用失败: {str(e)[:80]}")
                continue
            if df is None or df.empty:
                print("   ⚠️ 返回空")
                continue

            sub = df.rename(columns=str.lower).copy()
            sub["date"] = pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d")
            m = ths[ths["symbol"] == sym].merge(sub, on="date", how="inner",
                                                suffixes=("_ths", ""))
            if not len(m):
                print(f"   ⚠️ 与同花顺无交集（源返回 {len(sub)} 行）")
                continue

            # 价格：验复权口径
            rel = (m["close"].astype(float) - m["close_price"].astype(float)).abs() \
                / m["close_price"].astype(float)
            n_bad = int((rel > 1e-4).sum())
            print(f"   收盘价 vs 同花顺不复权 : 比对 {len(m)} 天，不符 {n_bad} 天 "
                  f"{'✅ 同一口径(不复权)' if n_bad == 0 else '❌ 口径不同!'}")

            # 量：验单位
            if "volume" in m.columns:
                rv = m["volume"].astype(float) / m["volume_ths"].astype(float)
                med = rv.median()
                if abs(med - 1) < 1e-3:
                    v = "✅ 单位=股（正确）"
                elif abs(med - 0.01) < 1e-4:
                    v = "❌ 单位=手（少 100 倍！）"
                elif abs(med - 100) < 1e-2:
                    v = "⚠️ 比值为 100（多 100 倍？）"
                else:
                    v = f"❓ 比值中位={med:.6f}"
                print(f"   成交量 vs 同花顺(股)   : 比值中位={med:.6f}  {v}")

            # 额：验单位（部分源不给 amount）
            if "amount" in m.columns and m["amount"].notna().any():
                ra = m["amount"].astype(float) / m["turnover"].astype(float)
                med = ra.dropna().median()
                if abs(med - 1) < 1e-3:
                    a = "✅ 单位=元（正确）"
                elif abs(med - 0.0001) < 1e-6:
                    a = "❌ 单位=万元（少 10000 倍！）"
                else:
                    a = f"❓ 比值中位={med:.6f}"
                print(f"   成交额 vs 同花顺(元)   : 比值中位={med:.6f}  {a}")
            else:
                print(f"   成交额                : 源未提供（同步代码写 NULL）→ 安全")

    hr("实时快照（腾讯 get_realtime，用于当日 amount/PE/PB）")
    for code in CASES[:2]:
        try:
            rt = tc.get_realtime(code)
            if rt:
                print(f"   {code}: price={rt.get('price')} volume={rt.get('volume')} "
                      f"amount={rt.get('amount')}（原始值，代码需 ×10000 转元）")
                d = ths[ths["symbol"] == code[2:]].sort_values("date").iloc[-1]
                amt_yuan = rt.get("amount", 0) * 10000.0
                print(f"        → ×10000 后 = {amt_yuan:,.0f} 元；"
                      f"同日同花顺 = {d['turnover']:,.0f} 元；"
                      f"比值 = {amt_yuan/d['turnover']:.4f}")
        except Exception as e:
            print(f"   {code} 失败: {str(e)[:60]}")

    hr("结论要点")
    print("   ① 价格与同花顺不复权一致 → 该源是不复权口径")
    print("   ② 成交量比值≈1 → 单位已是「股」（比值≈0.01 即为「手」，是 bug）")
    print("   ③ 成交额比值≈1 → 单位已是「元」（比值≈0.0001 即为「万元」，是 bug）")


if __name__ == "__main__":
    main()

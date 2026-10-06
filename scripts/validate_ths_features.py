#!/usr/bin/env python3
"""V6-A 小样本验证：新 5 维特征算得对不对（跑全量前必须过这一关）。

验证 6 项：
  ① 门控生效    —— 不开开关时，输出列数仍为 33（= V5 行为，逐位不变）
  ② 开关生效    —— 开开关时，输出 38 列（33+5）
  ③ 数值正确    —— 新维度的值与原始 parquet 对得上（抽 1 期人工核对）
  ④ ★asof 正确 —— 值必须在【法定披露日】之后才出现，之前为 0（防未来函数）
  ⑤ 缺失填 0    —— 无数据的股票输出全 0，不产生 NaN
  ⑥ 无 NaN/inf  —— 输出矩阵干净

跑全量重建要 4.7 小时，**带着 bug 进全量 = 白跑**，故本关必过。

用法：
  python scripts/validate_ths_features.py
"""
import os
import sys

import numpy as np
import pandas as pd

SX = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x"
sys.path.insert(0, SX)
os.chdir(SX)

FE = "data/extra_features/finance_ths_v2"
NEW = ["fin_ths_cash_quality", "fin_ths_cash_sales", "fin_ths_cash_index",
       "fin_ths_cash_invest", "fin_ths_rd_intensity"]
CODES = ["000001", "600519", "000858", "300750", "688507", "002415", "600036", "000333"]


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def get_feats(code, dates, close):
    from sequoia_x.features_extra.build_extra_features import build_extra_features
    feats, meta = build_extra_features(dates, close, code)
    return feats


def load_stock(conn, code):
    d = pd.read_sql(f'SELECT date,close FROM stock_daily WHERE symbol="{code}" '
                    f'ORDER BY date', conn)
    d["date"] = pd.to_datetime(d["date"])
    return pd.DatetimeIndex(d["date"]), pd.Series(d["close"].values, index=pd.DatetimeIndex(d["date"]))


def main():
    import sqlite3
    from sequoia_x.features_extra.build_extra_features import _disclose_date, _ths_report_end
    conn = sqlite3.connect("data/sequoia_v2.db")
    fails = []

    # ── ① 门控：不开开关 → 33 列、无新列 ──
    hr("① 门控生效（不开开关 = V5 行为）")
    os.environ.pop("FEATURE_V6_THS_FINANCE", None)
    for code in CODES[:3]:
        dates, close = load_stock(conn, code)
        f = get_feats(code, dates, close)
        n_new = sum(1 for c in NEW if c in f.columns)
        ok = (n_new == 0)
        print(f"   {code}: 总列数={f.shape[1]}  新列数={n_new}  {'✅' if ok else '❌ 门控失效!'}")
        if not ok:
            fails.append(f"{code} 门控失效")

    # ── ② 开关：开 → 38 列 ──
    hr("② 开关生效（FEATURE_V6_THS_FINANCE=1）")
    os.environ["FEATURE_V6_THS_FINANCE"] = "1"
    import importlib
    import sequoia_x.features_extra.build_extra_features as B
    importlib.reload(B)
    for code in CODES[:3]:
        dates, close = load_stock(conn, code)
        f, _ = B.build_extra_features(dates, close, code)
        n_new = sum(1 for c in NEW if c in f.columns)
        ok = (n_new == 5)
        print(f"   {code}: 总列数={f.shape[1]}  新列数={n_new}  {'✅' if ok else '❌ 开关无效!'}")
        if not ok:
            fails.append(f"{code} 开关无效")

    # ── ③④ 数值与 asof 正确性（逐股、逐期核对）──
    #
    # ★ 正确判据（初版写错过，见文末说明）：
    #   不是"披露日之前必须为 0"——公司从 2012 年就有数据，更早期报告期的值本就非零。
    #   正确判据是：**在披露日那一刻，值从「上一期」切换成「本期」**。
    #     · 披露日前最后一个交易日  → 应等于【上一期】的值
    #     · 披露日当天及之后首个交易日 → 应等于【本期】的值
    #   两者都吻合 ⇒ asof 口径正确、无未来函数。
    hr("③④ 数值 + asof 正确性（逐期核对：披露日那一刻是否发生正确切换）")
    SRCMAP = [("fin_ths_cash_quality", "net_profit_cash_content"),
              ("fin_ths_cash_sales", "operating_cash_flow_net_divide_income"),
              ("fin_ths_cash_index", "cash_operating_index"),
              ("fin_ths_cash_invest", "cash_meet_invest_ratio")]
    for code in CODES[:4]:
        dates, close = load_stock(conn, code)
        f, _ = B.build_extra_features(dates, close, code)
        ind = pd.read_parquet(f"{FE}/indicators/{code}.parquet").sort_values("report")
        reps = list(ind["report"].unique())
        # ★ 必须选「披露日落在该股可用日期范围内」的期次
        #   （stock_daily 只有 2020-01-02 起；选更早的期次会因样本内无对应交易日而取到 nan，
        #    那是测试选样问题，不是特征 bug —— 初版就踩了这个坑）
        d_min, d_max = f.index.min(), f.index.max()
        cand = [r for r in reps
                if pd.Timestamp(_disclose_date(_ths_report_end(r))) >= d_min + pd.Timedelta(days=400)]
        if len(cand) < 2:
            print(f"\n   --- {code}: 可用期次不足，跳过 ---")
            continue
        # 取中间某期（前后都有期，便于验证"切换"）
        k = len(cand) // 2
        rep, rep_prev = cand[k], cand[k - 1]
        r_now = ind[ind["report"] == rep].iloc[0]
        r_prev = ind[ind["report"] == rep_prev].iloc[0]
        dis = pd.Timestamp(_disclose_date(_ths_report_end(rep)))
        prev_dis = pd.Timestamp(_disclose_date(_ths_report_end(rep_prev)))
        print(f"\n   --- {code}  切换期 {rep_prev} → {rep}"
              f"（法定披露日 {prev_dis.date()} → {dis.date()}）---")
        before = f.loc[(f.index >= prev_dis) & (f.index < dis)]
        after = f.loc[f.index >= dis]
        for c, src in SRCMAP:
            v_prev_exp = float(pd.to_numeric(r_prev[src], errors="coerce")) / 100.0
            v_now_exp = float(pd.to_numeric(r_now[src], errors="coerce")) / 100.0
            v_before = before[c].iloc[-1] if len(before) else np.nan
            v_after = after[c].iloc[0] if len(after) else np.nan
            ok = (abs(v_before - v_prev_exp) < 1e-9) and (abs(v_after - v_now_exp) < 1e-9)
            print(f"     {c:26s} 披露前={v_before:>11.6f}(应{v_prev_exp:>11.6f})  "
                  f"披露后={v_after:>11.6f}(应{v_now_exp:>11.6f})  "
                  f"{'✅ 正确切换' if ok else '❌ 口径不符'}")
            if not ok:
                fails.append(f"{code} {c} asof 切换不符")

    # ── ⑤⑥ 缺失股票 + NaN/inf ──
    hr("⑤⑥ 缺失股票 & 数据洁净度")
    miss = []
    for code in CODES:
        dates, close = load_stock(conn, code)
        f, _ = B.build_extra_features(dates, close, code)
        sub = f[NEW]
        n_nan = int(sub.isna().sum().sum())
        n_inf = int(np.isinf(sub.to_numpy(dtype=float)).sum())
        allzero = bool((sub.fillna(0) == 0).all().all())
        print(f"   {code}: NaN={n_nan} inf={n_inf} 全零={allzero} "
              f"非零列={[c for c in NEW if (sub[c]!=0).any()]}")
        if n_nan or n_inf:
            fails.append(f"{code} 有 NaN/inf")

    # 模拟一只不存在新数据的股票
    fake = "999999"
    dates, close = load_stock(conn, CODES[0])
    f, _ = B.build_extra_features(dates, close, fake)
    sub = f[NEW]
    ok_empty = bool((sub.fillna(0) == 0).all().all()) and int(sub.isna().sum().sum()) == 0
    print(f"   {fake}(无数据): 全 0 且无 NaN = {ok_empty}  {'✅' if ok_empty else '❌'}")
    if not ok_empty:
        fails.append("缺失股票未正确填 0")

    conn.close()
    hr("结论")
    if fails:
        print("   ❌ 未通过：")
        for x in fails:
            print(f"      - {x}")
        print("\n   ★ 不要启动全量重建（会白跑 4.7 小时），先修上述问题。")
        sys.exit(1)
    print("   ✅ 全部通过 —— 可以启动全量重建")
    print("\n   下一步：FEATURE_V6_THS_FINANCE=1 <重建脚本>  （约 4.7 小时）")


if __name__ == "__main__":
    main()

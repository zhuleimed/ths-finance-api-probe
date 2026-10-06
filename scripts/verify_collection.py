#!/usr/bin/env python3
"""Step A 采集结果验证：finance_ths_v2 的数据质量体检。

四层检查：
  A 覆盖       —— 多少只 / 多少期 / 期数分布
  B 抓取完整性 —— ★最关键：抽样向接口逐季核对，验证「本地期数 == 接口能给的期数」
  C 字段完整度 —— 每个字段的非空率（找覆盖差的字段）
  D 与现有 finance 对拍 —— 重叠指标是否一致（决定能否安全拼接）

★ 关于 B 的判据演进（重要教训）：
  初版用「上市年份推算应有期数」判定少采，报了 25% 的股票 —— 实测证明**判据本身错了**。
  同花顺对相当一部分股票本身就没有完整历史（如 002151 北斗星通 2007 年上市，接口只给 2 期）。
  正确判据只有一个：**本地期数 == 接口实际能给的期数**。改成抽样核对后：8/8 一致，抓取完整。

用法：
  python scripts/verify_collection.py
"""
import glob
import json
import os
import sqlite3

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/extra_features/finance_ths_v2"
OLD = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/extra_features/finance"
DB = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db"
BASE = "https://fuyao.aicubes.cn"
SUBS = ["income", "balance", "cashflow", "indicators"]
# 004 现有 finance 列名 → 同花顺指标 index_id（同口径）
OVERLAP = {"净资产收益率": "index_weighted_avg_roe", "销售净利率": "sale_net_interest_ratio",
           "资产负债率": "assets_debt_ratio", "流动比率": "current_ratio",
           "速动比率": "quick_ratio"}
REP2DATE = {f"{y}-{q}": f"{y}-{['03-31','06-30','09-30','12-31'][q-1]}"
            for y in range(2010, 2031) for q in (1, 2, 3, 4)}


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main():
    rep = {}
    conn = sqlite3.connect(DB)
    listed = dict(conn.execute("SELECT symbol, listed_date FROM stock_list").fetchall())
    conn.close()

    hr("A 覆盖情况")
    frames = {}
    for s in SUBS:
        fs = glob.glob(os.path.join(FE, s, "*.parquet"))
        print(f"   {s:12s} {len(fs):>5,} 个文件", end="")
        if s == "indicators":
            # 抽样统计期数（全读太慢）
            import random
            random.seed(0)
            samp = random.sample(fs, min(300, len(fs)))
            ns = [len(pd.read_parquet(f, columns=["report"])) for f in samp]
            print(f"   期数 中位={int(np.median(ns))} 最小={min(ns)} 最大={max(ns)}（抽 {len(samp)} 只）")
        else:
            samp = fs[:300]
            ns = [len(pd.read_parquet(f, columns=["fiscal_year"])) for f in samp]
            print(f"   期数 中位={int(np.median(ns))} 最小={min(ns)} 最大={max(ns)}（抽 {len(samp)} 只）")
    print(f"\n   总计 {sum(len(glob.glob(os.path.join(FE,s,'*.parquet'))) for s in SUBS):,} 个文件")

    hr("B ★抓取完整性核对（判据：本地期数 vs 接口实测能给的期数）")
    print("   说明：早先版本用「上市年份推算应有期数」做判据，实测证明**该判据是错的** ——")
    print("         同花顺对部分股票本身就没有完整历史（如 002151 北斗星通上市 2007，")
    print("         接口只给 2 期）。正确判据是：**本地期数 == 接口能给的期数**。")
    fs = glob.glob(os.path.join(FE, "indicators", "*.parquet"))
    local_n = {}
    for f in fs:
        code = os.path.basename(f)[:-8]
        try:
            local_n[code] = len(pd.read_parquet(f, columns=["report"]))
        except Exception:
            pass
    v = pd.DataFrame([{"code": k, "n": n} for k, n in local_n.items()])
    print(f"\n   本地共 {len(v):,} 只，期数 中位={v['n'].median():.0f} "
          f"均值={v['n'].mean():.1f} 最小={v['n'].min()} 最大={v['n'].max()}")
    q = v["n"].quantile([.05, .25, .5, .75, .95]).to_dict()
    print(f"   分位: p5={q[0.05]:.0f} p25={q[0.25]:.0f} p50={q[0.5]:.0f} "
          f"p75={q[0.75]:.0f} p95={q[0.95]:.0f}")

    # 抽查期数最少的若干只，逐个向接口核对（决定性检验）
    import requests
    KEY = os.environ.get("HITHINK_FINANCE_API_KEY", "")
    if not KEY:
        try:
            import dotenv
            dotenv.load_dotenv("/public/home/hpc/zhulei/superman/quant/code/"
                               "017_workbuddy/004_sequoia-x/.env")
            KEY = os.environ.get("HITHINK_FINANCE_API_KEY", "")
        except Exception:
            pass
    probe = v.nsmallest(8, "n")["code"].tolist()
    print(f"\n   抽查期数最少的 8 只，向接口逐季核对：")
    if not KEY:
        print("     ⚠️ 无 API Key，跳过核对")
    else:
        mism = 0
        for code in probe:
            ths = code + (".SH" if code.startswith(("60", "68")) else ".SZ")
            cnt = 0
            for y in range(2012, 2027):
                for qq in (1, 2, 3, 4):
                    r = requests.get(f"{BASE}/api/a-share/financials/indicators",
                                     params={"thscode": ths, "report": f"{y}-{qq}"},
                                     headers={"X-api-key": KEY}, timeout=20).json()
                    if r.get("code") == 0 and any(
                            i.get("value") not in (None, "", "null")
                            for a in r.get("data", {}).get("abilities", [])
                            for i in a.get("indicators", [])):
                        cnt += 1
            loc = local_n.get(code, -1)
            ok = loc == cnt
            mism += 0 if ok else 1
            print(f"     {code}  本地 {loc:>3} 期 / 接口 {cnt:>3} 期  "
                  f"{'✅一致' if ok else '❌不一致'}")
        print(f"\n   → {'✅ 抓取完整（本地 == 接口）' if mism == 0 else f'❌ {mism} 只不一致，需重采'}")
    rep["collection_integrity"] = {"n_stocks": len(v),
                                   "median_periods": float(v["n"].median())}

    hr("C 字段完整度")
    for s in SUBS:
        fs = glob.glob(os.path.join(FE, s, "*.parquet"))
        frames[s] = pd.concat([pd.read_parquet(f) for f in fs[:200]], ignore_index=True)
        df = frames[s]
        meta = {"code", "thscode", "ticker", "period", "fiscal_year", "fiscal_period",
                "report_date_ms", "period_end_ms", "currency", "report"}
        val = [c for c in df.columns if c not in meta]
        cov = df[val].notna().mean().sort_values()
        print(f"\n   【{s}】{len(val)} 个数值字段（抽 200 只 / {len(df):,} 行）")
        print(f"     完整度 最低 5 个: " +
              ", ".join(f"{c}={cov[c]*100:.0f}%" for c in cov.index[:5]))
        print(f"     完整度 最高 5 个: " +
              ", ".join(f"{c}={cov[c]*100:.0f}%" for c in cov.index[-5:]))

    hr("D 与现有 finance 对拍（重叠指标，决定能否安全拼接）")
    v = frames["indicators"].copy()
    v["date"] = v["report"].map(REP2DATE)
    res = []
    for col, iid in OVERLAP.items():
        if iid not in v.columns:
            continue
        n_cmp = n_eq = 0
        for code, grp in v.groupby("code"):
            f = os.path.join(OLD, f"{code}.parquet")
            if not os.path.exists(f):
                continue
            try:
                o = pd.read_parquet(f, columns=["报告期", col])
            except Exception:
                continue
            j = grp[["date", iid]].merge(o, left_on="date", right_on="报告期")
            if not len(j):
                continue
            a = pd.to_numeric(j[iid], errors="coerce")
            b = pd.to_numeric(j[col].astype(str).str.replace("%", ""), errors="coerce")
            ok = a.notna() & b.notna()
            n_cmp += int(ok.sum())
            n_eq += int((a[ok] - b[ok]).abs().lt(0.01).sum())
        if n_cmp:
            res.append((col, n_cmp, n_eq))
            print(f"   {col:12s} 可比 {n_cmp:>7,} 期  逐值一致 {n_eq:>7,} "
                  f"({n_eq/n_cmp*100:6.2f}%)")
    if res:
        tot = sum(r[1] for r in res); eq = sum(r[2] for r in res)
        print(f"\n   合计 可比 {tot:,} 期，一致 {eq:,} ({eq/tot*100:.2f}%)")
        print(f"   → {'✅ 同源同口径，可安全拼接' if eq/tot > 0.95 else '⚠️ 一致性偏低，拼接前需细查'}")

    json.dump(rep, open(os.path.join(ROOT, "data", "collection_verify.json"), "w"),
              ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main()

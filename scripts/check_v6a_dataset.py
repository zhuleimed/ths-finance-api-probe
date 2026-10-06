#!/usr/bin/env python3
"""V6-A 阶段一验收：检查两臂数据集缓存是否正确、可比。

在启动重训/回测**之前**跑这个。检查 7 项，任一不过就不该往下走。

  ① 两臂缓存都已生成（今日 created、sample_end=2026-09-30）
  ② ★维度正确：A=129、B=134（开关生效的硬证据）
  ③ 样本数与股票数一致（否则两臂不同源，不可比）
  ④ 采样日期序列完全一致（同上）
  ⑤ 新增 5 列非退化（非全 0、有方差）——特征真的进去了
  ⑥ 新增 5 列无 NaN/inf
  ⑦ 前 129 列两臂一致（确保只在末尾追加，没动原有特征）

用法：python scripts/check_v6a_dataset.py
退出码：0=通过可继续  1=有问题
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path("/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x")
CACHE = ROOT / "data/cache/v2_dataset"
NEW_COLS = ["fin_ths_cash_quality", "fin_ths_cash_sales", "fin_ths_cash_index",
            "fin_ths_cash_invest", "fin_ths_rd_intensity"]
TODAY = "2026-10-06"


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def find_caches():
    """按 metadata 找出今天新建的 v5 / v6 缓存。"""
    out = {}
    for d in sorted(CACHE.glob("*/")):
        mf = d / "metadata.json"
        if not mf.exists():
            continue
        try:
            m = json.loads(mf.read_text())
        except Exception:
            continue
        p = m.get("params", {})
        if not str(m.get("created", "")).startswith(TODAY):
            continue
        fv = p.get("feature_version")
        if p.get("sample_end") != "2026-09-30":
            continue
        out.setdefault(fv, []).append((d, m))
    return out


def main():
    fails = []

    hr("① 定位今日新建的两臂缓存")
    caches = find_caches()
    for fv in (5, 6):
        lst = caches.get(fv, [])
        print(f"   feature_version={fv}: 找到 {len(lst)} 个")
        for d, m in lst:
            print(f"     {d.name}  X={m.get('X_shape')}  created={m.get('created','')[:19]}")
    if 5 not in caches or 6 not in caches:
        print("\n   ❌ 两臂缓存未齐（可能重建尚未完成）")
        sys.exit(1)

    A_dir, A_meta = caches[5][0]
    B_dir, B_meta = caches[6][0]
    print(f"\n   A 臂(129维) = {A_dir.name}")
    print(f"   B 臂(134维) = {B_dir.name}")

    hr("② 维度正确性（开关是否真的生效）")
    sa = tuple(A_meta["X_shape"])
    sb = tuple(B_meta["X_shape"])
    print(f"   A X_shape = {sa}")
    print(f"   B X_shape = {sb}")
    ok = (len(sa) == 3 and len(sb) == 3 and sa[2] == 129 and sb[2] == 134
          and sa[0] == sb[0] and sa[1] == sb[1])
    print(f"   {'✅ 正确（A=129, B=134，样本数/窗口一致）' if ok else '❌ 不符合预期'}")
    if not ok:
        fails.append(f"维度异常 A={sa} B={sb}")

    hr("③ 样本数与股票数一致")
    n_same = A_meta["n_samples"] == B_meta["n_samples"]
    st_same = A_meta["params"]["n_stocks"] == B_meta["params"]["n_stocks"]
    print(f"   样本数   A={A_meta['n_samples']:,}  B={B_meta['n_samples']:,}  "
          f"{'✅' if n_same else '❌'}")
    print(f"   股票数   A={A_meta['params']['n_stocks']:,}  "
          f"B={B_meta['params']['n_stocks']:,}  {'✅' if st_same else '❌'}")
    if not (n_same and st_same):
        fails.append("两臂样本数/股票数不一致 → 不同源，不可比")

    hr("④ 采样日期序列一致")
    da = json.loads((A_dir / "dates.json").read_text())
    db = json.loads((B_dir / "dates.json").read_text())
    same = (da == db)
    print(f"   长度 A={len(da)}  B={len(db)}   完全一致={same}  {'✅' if same else '❌'}")
    print(f"   范围 {da[0]} ~ {da[-1]}")
    if not same:
        fails.append("两臂采样日期不一致")

    hr("⑤⑥ 新增 5 列：非退化 + 无 NaN/inf")
    # 用 mmap 只读需要的列，避免加载 25GB×2
    Xb = np.load(B_dir / "X.npy", mmap_mode="r")
    sub = np.asarray(Xb[:, :, 129:134])          # 最后 5 列
    print(f"   取 B 臂最后 5 列，shape={sub.shape}")
    for i, name in enumerate(NEW_COLS):
        col = sub[:, :, i]
        nz = float((col != 0).mean())
        n_nan = int(np.isnan(col).sum())
        n_inf = int(np.isinf(col).sum())
        std = float(np.nanstd(col))
        good = (nz > 0.01) and (n_nan == 0) and (n_inf == 0) and (std > 1e-8)
        print(f"     {name:26s} 非零率={nz*100:6.2f}%  std={std:10.6f}  "
              f"NaN={n_nan} inf={n_inf}  {'✅' if good else '❌'}")
        if not good:
            fails.append(f"{name} 退化或有脏值（非零率={nz:.4f} std={std}）")

    hr("⑦ 前 129 列两臂一致（确保只追加、没动原有特征）")
    Xa = np.load(A_dir / "X.npy", mmap_mode="r")
    n_sample = min(2000, Xa.shape[0])
    idx = np.linspace(0, Xa.shape[0] - 1, n_sample).astype(int)
    a_part = np.asarray(Xa[idx, :, :129])
    b_part = np.asarray(Xb[idx, :, :129])
    diff = np.abs(a_part - b_part)
    mx = float(diff.max())
    n_diff = int((diff > 1e-9).sum())
    print(f"   抽样 {n_sample} 个样本 × 120 天 × 129 列")
    print(f"   最大绝对差={mx:.6e}   超过 1e-9 的元素数={n_diff}")
    print(f"   {'✅ 两臂前 129 列逐位一致（只追加了新列）' if n_diff == 0 else '⚠️ 存在差异，需排查'}")
    if n_diff:
        fails.append(f"前 129 列不一致（{n_diff} 个元素）")

    hr("结论")
    if fails:
        print("   ❌ 未通过，不要往下走：")
        for f in fails:
            print(f"      - {f}")
        sys.exit(1)
    print("   ✅ 全部通过 —— 两臂同源、维度正确、新特征非退化")
    print("   → 可以启动阶段二三（重训 + 回测）")
    sys.exit(0)


if __name__ == "__main__":
    main()

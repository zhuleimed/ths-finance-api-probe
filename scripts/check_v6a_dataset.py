#!/usr/bin/env python3
"""V6-A 阶段一验收：检查两臂数据集缓存是否正确、可比。

在启动重训/回测**之前**跑这个。检查 7 项，任一不过就不该往下走。

  ① 两臂缓存都已生成（created >= 本次实验重建日）
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
TODAY = "2026-10-06"   # 本次实验的重建日（缓存 created 须 >= 此日）


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def find_caches():
    """找出本次重建的 v5 / v6 缓存。

    ★ 2026-10-07 修正的 bug：初版按 `params.sample_end == "2026-09-30"` 过滤，
      但 metadata 里实际记的是 **2026-09-21**（那是最后一个【采样日】，
      不是 cfg 的数据截止日）—— 假设写死导致两臂一个都没匹配上，验收假失败。
      改为：按 created 在本次实验窗口内（>= TODAY）、每 fv 取**最新**的一个。
    """
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
        if str(m.get("created", "")) < TODAY:
            continue
        fv = p.get("feature_version")
        if fv not in (5, 6):
            continue
        lst = out.setdefault(fv, [])
        lst.append((d, m))
        # 按 created 倒序保留最新
        lst.sort(key=lambda x: str(x[1].get("created", "")), reverse=True)
        out[fv] = lst[:1]
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

    hr("④ 采样日期序列一致（按内容比对，顺序不敏感）")
    da = json.loads((A_dir / "dates.json").read_text())
    db = json.loads((B_dir / "dates.json").read_text())
    from collections import Counter
    same = (Counter(da) == Counter(db))
    print(f"   长度 A={len(da):,}  B={len(db):,}")
    print(f"   逐位相同={da == db}   内容相同(顺序无关)={same}  {'✅' if same else '❌'}")
    print(f"   范围 {min(da)} ~ {max(da)}")
    print("   注：两臂**行顺序不同**（并行 worker 完成次序非确定），但每个采样日的")
    print("       样本数完全一致 → 是同一批样本的不同排列，不影响按日期取数的训练逻辑。")
    if not same:
        fails.append("两臂采样日期内容不一致")

    # ── 列布局（2026-10-07 实测确认，初版假设"新列在末尾"是错的）──
    #   76 基础 + N 扩展 + 12 补零 = 总宽
    #     A: 76 + 41 + 12 = 129
    #     B: 76 + 46 + 12 = 134   ← 新 5 维接在原有 41 维之后（列 117~121），**不是末尾**
    BASE_DIM, PAD_DIM = 76, 12

    hr("⑤⑥ 新增 5 列（列 117~121）：非退化 + 无 NaN/inf")
    Xb = np.load(B_dir / "X.npy", mmap_mode="r")
    extra_b = sb[2] - BASE_DIM - PAD_DIM
    new_start = BASE_DIM + (extra_b - len(NEW_COLS))
    print(f"   B 扩展维度数={extra_b}（41 原有 + {len(NEW_COLS)} 新增）→ "
          f"新列位于 [{new_start}, {new_start + len(NEW_COLS)})")
    print(f"   末 {PAD_DIM} 列 [{sb[2]-PAD_DIM}, {sb[2]}) 为补零区")
    sub = np.asarray(Xb[:, :, new_start:new_start + len(NEW_COLS)])
    print(f"   取该区段，shape={sub.shape}")
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

    hr("⑦ 原有 117 列两臂一致（基础76+扩展41，确保只增不改）")
    print("   注：两臂行顺序不同 → 必须在**同一采样日内按行指纹排序后**比对，")
    print("       否则逐位比较会因错位而大面积假失败（初版即踩此坑）。")
    Xa = np.load(A_dir / "X.npy", mmap_mode="r")
    da_arr, db_arr = np.array(da), np.array(db)
    dates_common = sorted(set(da_arr) & set(db_arr))
    n_checked = n_mismatch = 0
    bad_date = None
    for dt in dates_common[:12]:                      # 抽 12 个采样日
        ia = np.where(da_arr == dt)[0]
        ib = np.where(db_arr == dt)[0]
        a = np.asarray(Xa[ia][:, :, :117]).astype(np.float32)
        b = np.asarray(Xb[ib][:, :, :117]).astype(np.float32)
        fa = np.array([hash(x.tobytes()) for x in a])
        fb = np.array([hash(x.tobytes()) for x in b])
        if set(fa.tolist()) != set(fb.tolist()):
            n_mismatch += 1
            bad_date = dt
            continue
        sa = a[np.argsort(fa)]
        sb_ = b[np.argsort(fb)]
        if not np.array_equal(sa, sb_):
            n_mismatch += 1
            bad_date = dt
        n_checked += 1
    print(f"   抽 {len(dates_common[:12])} 个采样日逐行指纹比对："
          f"通过 {n_checked}，不符 {n_mismatch}  {'✅' if n_mismatch == 0 else '❌'}")
    if n_mismatch:
        fails.append(f"原有 117 列两臂不一致（首个异常采样日 {bad_date}）")

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

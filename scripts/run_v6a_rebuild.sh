#!/usr/bin/env bash
# V6-A 实验 · 第一阶段：A/B 两臂数据集缓存重建（并行）
#
# 背景：2026-10-06 修复了 004 的 40,027 行复权价格 / 68,781 行量额 / 129 行污染。
#       现有 A 臂缓存建于 10-01，用的是**修复前数据** —— 直接用会让 A/B 差异
#       混入"数据修复"的影响。故两臂**都在修复后的数据上重建**，才是严格对照。
#
# 为什么能并行：缓存 key 含 feature_version（v5 / v6）→ 写入**不同目录**，互不干扰。
#   资源：2 进程 × 12 workers = 24 ≤ 36 核；内存 ~26GB/份 × 2 = 52GB ≤ 187GB。
#
# 耗时：单臂约 4.7 小时（代码实测注释值），并行后总时长 ≈ 4.7 小时。
#
# 用法：nohup bash scripts/run_v6a_rebuild.sh > logs/v6a_rebuild_$(date +%Y%m%d_%H%M).log 2>&1 &

set -u
SX=/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x
PY=/home/zhulei/anaconda3/envs/zhulei_py312/bin/python
cd "$SX" || exit 1

mkdir -p logs
ts() { date '+%Y-%m-%d %H:%M:%S'; }

echo "=============================================================="
echo "[$(ts)] V6-A 实验 · 两臂缓存重建（并行）启动"
echo "  项目: $SX"
echo "  A 臂: FEATURE_VERSION=5  → 129 维（现有生产特征）"
echo "  B 臂: FEATURE_VERSION=6  → 134 维（+ 同花顺财务质量 5 维）"
echo "  数据: 2026-10-06 修复后的 stock_daily"
echo "=============================================================="

# ── 前置自检：确认开关确实能改变版本号 ──
V_DEFAULT=$($PY -c "import sys;sys.path.insert(0,'.');from sequoia_x.model_selection_v2.labels import FEATURE_VERSION as v;print(v)")
V_ON=$(FEATURE_V6_THS_FINANCE=1 $PY -c "import sys;sys.path.insert(0,'.');from sequoia_x.model_selection_v2.labels import FEATURE_VERSION as v;print(v)")
echo "[$(ts)] 自检 FEATURE_VERSION: 默认=$V_DEFAULT  开关开=$V_ON"
if [ "$V_DEFAULT" != "5" ] || [ "$V_ON" != "6" ]; then
  echo "[$(ts)] ❌ 自检失败：版本号不符合预期（应 5 / 6）。中止。"
  exit 2
fi

# ★★ 必须设 V4_REUSE_SAFE_DAYS：否则增量复用会搬用"修复前算的"旧采样日
#    （实测首次启动时 A 臂复用了旧缓存 3ccb10244903 的 406,543 样本、只重算 4 天，
#     与 B 臂数据不同源 → 失去可比性）。设成极大值 → 触发「转全量构建」分支。
export V4_REUSE_SAFE_DAYS=99999

# ── 启动 A 臂 ──
echo "[$(ts)] ── 启动 A 臂（v5, 129 维，强制全量重建）──"
$PY scripts/rebuild_dataset_cache.py --only-88 --workers 12 \
  > logs/v6a_A_arm_rebuild.log 2>&1 &
PID_A=$!
echo "[$(ts)]   A 臂 PID=$PID_A  日志=logs/v6a_A_arm_rebuild.log"

# 错开 60 秒启动 B 臂，避免同时读同一批 parquet 造成 IO 尖峰
sleep 60

# ── 启动 B 臂 ──
echo "[$(ts)] ── 启动 B 臂（v6, 134 维，强制全量重建）──"
FEATURE_V6_THS_FINANCE=1 $PY scripts/rebuild_dataset_cache.py --only-88 --workers 12 \
  > logs/v6a_B_arm_rebuild.log 2>&1 &
PID_B=$!
echo "[$(ts)]   B 臂 PID=$PID_B  日志=logs/v6a_B_arm_rebuild.log"

echo "[$(ts)] 两臂已启动，等待完成…（预计 ~4.7 小时）"

# ── 等待 ──
RC_A=0; RC_B=1
wait $PID_A || RC_A=$?
echo "[$(ts)] A 臂结束  exit=$RC_A"
wait $PID_B || RC_B=$?
echo "[$(ts)] B 臂结束  exit=$RC_B"

echo "=============================================================="
if [ "$RC_A" -eq 0 ] && [ "$RC_B" -eq 0 ]; then
  echo "[$(ts)] ✅ 两臂缓存重建全部完成"
else
  echo "[$(ts)] ❌ 有臂失败：A=$RC_A B=$RC_B（详见各自日志）"
fi
echo "  产物目录: data/cache/v2_dataset/（按 hash 分子目录）"
ls -lt data/cache/v2_dataset/ 2>/dev/null | head -6
echo "  下一步（需人工确认后执行）：build_prediction_cache + A/B 回测"
echo "=============================================================="
exit $(( RC_A + RC_B ))

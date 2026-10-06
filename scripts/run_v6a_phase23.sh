#!/usr/bin/env bash
# V6-A 实验 · 第二阶段（预测缓存）+ 第三阶段（回测）—— A/B 串行
#
# ★ 为什么串行不并行：`t4_checkpoint_cache_{月}.keras` 只用月份做 key，
#   deep_lstm.py 见到文件就 load_model（断点续跑机制）⇒ 并行跑两臂时，
#   B 臂会**静默复用 A 臂的 T4 模型**。这是待办 #11 记录过的事故类型。
#   串行则无此问题（且 T4 输入 80 维两臂相同，复用等价模型无害）。
#
# 耗时依据（实测，非估算）：
#   70 月预测缓存含 T4 = 221min（logs: "耗时 221min ｜ 输出缓存 69/70 个月"）
#   回测 72 组 ≈ 20~40min
#   ⇒ 每臂约 4.0~4.4h，两臂串行约 8~9h
#
# 用法：nohup bash scripts/run_v6a_phase23.sh > logs/v6a_phase23_$(date +%Y%m%d_%H%M).log 2>&1 &

set -u
SX=/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x
PY=/home/zhulei/anaconda3/envs/zhulei_py312/bin/python
CHK=/public/home/hpc/zhulei/superman/quant/code/023_THS/scripts/check_v6a_dataset.py
cd "$SX" || exit 1
mkdir -p logs
ts() { date '+%Y-%m-%d %H:%M:%S'; }
T_START=$(date +%s)

# 口径（可按需改）
PERIOD_FLAG="--period full"          # 70 月（2020-09~2026-06），与项目评估纪律一致
START_MONTH=2020-09
END_MONTH=2026-06
OUTDIR=output/backtest_v2

echo "=============================================================="
echo "[$(ts)] V6-A 阶段二三启动（A/B 串行）"
echo "  口径: $PERIOD_FLAG  月份 $START_MONTH ~ $END_MONTH"
echo "  预期: 每臂 ~4.0-4.4h，两臂 ~8-9h"
echo "=============================================================="

# ── 前置：数据集验收（不过就停）──
echo "[$(ts)] ── 前置验收：检查两臂数据集缓存 ──"
$PY "$CHK" 2>&1 | tee logs/v6a_dataset_check.log
RC_CHK=${PIPESTATUS[0]}
if [ "$RC_CHK" -ne 0 ]; then
  echo "[$(ts)] ❌ 数据集验收未通过（exit=$RC_CHK），中止。详见 logs/v6a_dataset_check.log"
  exit 2
fi
echo "[$(ts)] ✅ 数据集验收通过"

run_arm() {  # $1=A/B  $2=预测缓存输出  $3=回测输出目录  $4=环境变量前缀
  local arm=$1 pcache=$2 odir=$3 envpfx=$4
  echo ""
  echo "=============================================================="
  echo "[$(ts)] ══ $arm 臂 开始 ══"
  echo "=============================================================="

  echo "[$(ts)] [$arm] 阶段二：构建 70 月预测缓存 → $pcache"
  local t0=$(date +%s)
  env $envpfx $PY scripts/build_prediction_cache.py \
      --start-month $START_MONTH --end-month $END_MONTH \
      --output "$pcache" > "logs/v6a_${arm}_predcache.log" 2>&1
  local rc1=$?
  echo "[$(ts)] [$arm] 阶段二结束 exit=$rc1 耗时 $(( ($(date +%s)-t0)/60 ))min"
  if [ $rc1 -ne 0 ]; then
    echo "[$(ts)] ❌ [$arm] 预测缓存构建失败，详见 logs/v6a_${arm}_predcache.log"
    return $rc1
  fi

  echo "[$(ts)] [$arm] 阶段三：回测 72 组 → $odir"
  local t1=$(date +%s)
  env $envpfx $PY scripts/run_shared_backtest.py \
      --all $PERIOD_FLAG --cache "$pcache" --output-dir "$odir" \
      > "logs/v6a_${arm}_backtest.log" 2>&1
  local rc2=$?
  echo "[$(ts)] [$arm] 阶段三结束 exit=$rc2 耗时 $(( ($(date +%s)-t1)/60 ))min"
  if [ $rc2 -ne 0 ]; then
    echo "[$(ts)] ❌ [$arm] 回测失败，详见 logs/v6a_${arm}_backtest.log"
    return $rc2
  fi

  echo "[$(ts)] ✅ $arm 臂全部完成（总计 $(( ($(date +%s)-t0)/60 ))min）"
  ls -la "$odir"/summary_all.csv 2>/dev/null | sed 's/^/     /'
  return 0
}

# ── A 臂：v5 / 129 维 ──
run_arm A "$OUTDIR/v6a_pred_A_129.json" "$OUTDIR/v6a_A_129" ""
RC_A=$?

# ── B 臂：v6 / 134 维 ──
if [ $RC_A -eq 0 ]; then
  run_arm B "$OUTDIR/v6a_pred_B_134.json" "$OUTDIR/v6a_B_134" "FEATURE_V6_THS_FINANCE=1"
  RC_B=$?
else
  echo "[$(ts)] ⚠️ A 臂失败，跳过 B 臂（避免产出不可比的结果）"
  RC_B=99
fi

TOTAL=$(( ($(date +%s)-T_START)/60 ))
echo ""
echo "=============================================================="
if [ "$RC_A" -eq 0 ] && [ "${RC_B:-99}" -eq 0 ]; then
  echo "[$(ts)] ✅ 全部完成，总耗时 ${TOTAL}min"
  echo "  A 臂结果: $OUTDIR/v6a_A_129/summary_all.csv"
  echo "  B 臂结果: $OUTDIR/v6a_B_134/summary_all.csv"
  echo "  → 对比这两份 CSV 的 TOP-N 超额，即为本实验的结论"
else
  echo "[$(ts)] ❌ 有失败：A=$RC_A B=${RC_B:-未跑}（总耗时 ${TOTAL}min）"
fi
echo "=============================================================="
exit $(( RC_A + ${RC_B:-99} ))

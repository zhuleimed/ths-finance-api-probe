#!/usr/bin/env bash
# V6-A 自动接力：等重建结束 → 跑验收 → 通过则自动启动阶段二三
#
# 为什么要有这一层：重建约 19:41 完成，用户约 19:50 才进会话。
#   若这 9 分钟内会话断开 / 用户来不了，整晚就白等。本脚本让**结果不依赖会话在线**。
#   用户 19:50 进来时看到的是「验收结果 + 已开跑」，随时可叫停。
#
# ★ 等待方式用「PID 存活」而非 `pgrep -f`：
#   004 的待办里记录过一次事故——接力脚本用 pgrep -f 匹配进程，被自己写的监听器
#   互相匹配，结果构建 11:58 就完成了、接力却空等 20 小时。用 PID 彻底避开。
#
# 用法：nohup bash scripts/run_v6a_chain.sh <重建启动器PID> > logs/v6a_chain_$(date +%Y%m%d_%H%M).log 2>&1 &

set -u
SX=/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x
BASE=/public/home/hpc/zhulei/superman/quant/code/023_THS/scripts
PY=/home/zhulei/anaconda3/envs/zhulei_py312/bin/python
WAIT_PID="${1:-483194}"
cd "$SX" || exit 1
mkdir -p logs
ts() { date '+%Y-%m-%d %H:%M:%S'; }

echo "[$(ts)] ══ V6-A 自动接力启动 ══"
echo "  等待重建进程 PID=$WAIT_PID 结束…"

# ── 1. 等重建结束 ──
# 用「PID 存活 + 进程身份」双重判据，并加 12 小时硬上限：
#   ① 只用 kill -0 有 **PID 复用** 风险：若目标进程已死、而系统把该 PID 分配给
#      别的进程，kill -0 仍返回成功 → 接力会**永远等下去** → 整晚白等。
#   ② 故再核一次进程名（args 含 run_v6a_rebuild）确认身份。
#   ③ 再加 MAX_WAIT 兜底：无论什么原因，12 小时后一定往下走，绝不无限等。
MAX_WAIT=$((12 * 3600))   # 12h：B 臂实测跑 7h，原 6h 上限太紧
waited=0
while [ "$waited" -lt "$MAX_WAIT" ]; do
  if ! kill -0 "$WAIT_PID" 2>/dev/null; then
    echo "[$(ts)] PID $WAIT_PID 已消失（等待 ${waited}s）"
    break
  fi
  if ! ps -p "$WAIT_PID" -o args= 2>/dev/null | grep -q "run_v6a_rebuild"; then
    echo "[$(ts)] ⚠️ PID $WAIT_PID 仍在，但已不是重建进程（疑似 PID 复用）→ 视为已结束"
    break
  fi
  sleep 60
  waited=$((waited + 60))
  if [ $((waited % 1800)) -eq 0 ]; then
    echo "[$(ts)]   仍在等重建… 已等 $((waited/60))min"
  fi
done
if [ "$waited" -ge "$MAX_WAIT" ]; then
  echo "[$(ts)] ⚠️ 等待超过 ${MAX_WAIT}s 上限 → 强制继续（请人工确认重建是否真的完成）"
fi
sleep 20   # 留缓冲，确保子进程也写完文件

echo "[$(ts)] 重建进程已结束。检查产物…"
ls -lt data/cache/v2_dataset/ 2>/dev/null | head -4 | sed 's/^/   /'

# ── 2. 跑验收 ──
echo "[$(ts)] ── 阶段一验收 ──"
$PY "$BASE/check_v6a_dataset.py" 2>&1 | tee logs/v6a_dataset_check.log
RC_CHK=${PIPESTATUS[0]}

if [ "$RC_CHK" -ne 0 ]; then
  echo ""
  echo "[$(ts)] ⛔ 数据集验收未通过（exit=$RC_CHK）——**不启动阶段二三**"
  echo "   请人工查看 logs/v6a_dataset_check.log 后再决定。"
  echo "   若确认无碍，手动执行："
  echo "     nohup bash $BASE/run_v6a_phase23.sh > logs/v6a_phase23_\$(date +%Y%m%d_%H%M).log 2>&1 &"
  exit "$RC_CHK"
fi

echo ""
echo "[$(ts)] ✅ 验收通过 → 自动启动阶段二三（A/B 串行，预计 ~8.5 小时）"
echo "   若此时想停：pkill -f run_v6a_phase23.sh && pkill -f build_prediction_cache.py"

# ── 3. 启动阶段二三（独立进程，本脚本退出也不影响它）──
nohup bash "$BASE/run_v6a_phase23.sh" \
  > "logs/v6a_phase23_$(date +%Y%m%d_%H%M).log" 2>&1 &

PID23=$!
echo "[$(ts)] 阶段二三 PID=$PID23  日志=logs/v6a_phase23_*.log"
sleep 10
kill -0 "$PID23" 2>/dev/null && echo "[$(ts)] ✅ 阶段二三已确认在跑" \
                            || echo "[$(ts)] ⚠️ 阶段二三未存活，请查日志"
echo "[$(ts)] 接力完成，本脚本退出。"

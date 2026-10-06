# 004 数据修复 · 备份清单

> 自动生成：2026-10-06 12:03:57 ｜ 生成器：`scripts/gen_backup_manifest.py`
> 目录：`023_THS/data/backup/`（已 gitignore，不入库）

---

## ⚠️ 使用前必读（重要）

**这些 `.db.bak` 是「修复前」的快照——里面装的仍然是错误数据。**

| | |
|---|---|
| ✅ **可以做的事** | 如果发现修复改错了，用它覆盖回去，**退回修复前**的状态 |
| ❌ **不能做的事** | 把它当作「干净备份」来恢复/参考——它不是干净版本，是**事故现场照片** |

---

## 一、整库副本（`.db.bak`）——「整栋楼拍照」

每次修复前复制整个数据库（1.8GB）。要回滚时，把它覆盖回生产库路径即可**整体退回**。

| 备份文件 | 对应修复 | 规模 | 修复脚本 | 大小 |
|---|---|---|---|---|
| `sequoia_v2_20261006_094130.db.bak` | 2026-07-06 单日污染 | 129 行 | `scripts/fix_20260706_corruption.py` | 1.75 GB |
| `sequoia_v2_20261006_103622.db.bak` | 复权口径错误 | 40,027 行价格 | `scripts/fix_adjusted_rows.py` | 1.75 GB |
| `sequoia_v2_20261006_115305.db.bak` | volume/amount 单位错乱 | 68,781 行 | `scripts/fix_volume_amount.py` | 1.75 GB |

**回滚方法**（以退回第二次修复前为例）：

```bash
# 1. 先停掉所有读写 004 库的进程（流水线/回测）
# 2. 把当前库另存一份（万一还想再回来）
cp /public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db /public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db.before_rollback
# 3. 用备份覆盖
cp /public/home/hpc/zhulei/superman/quant/code/023_THS/data/backup/sequoia_v2_20261006_103622.db.bak /public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db
```

---

## 二、逐行快照（`.csv`）——「只拍动过的那几块砖」

只导出**被修改的行**的原始值。回滚更精准：只改回那几行，不碰其他 750 万行。

| CSV 文件 | 内容 | 行数 | 大小 |
|---|---|---|---|
| `stock_daily_20260706_20261006_093816.csv` | 2026-07-06 被改的 129 行原始值 | 129 | 0.0 MB |
| `stock_daily_20260706_20261006_094130.csv` | 2026-07-06 被改的 129 行原始值 | 129 | 0.0 MB |
| `stock_daily_price_20261006_102944.csv` | 被改的 40,027 行原始价格（open/high/low/close） | 40,027 | 2.8 MB |
| `stock_daily_price_20261006_103622.csv` | 被改的 40,027 行原始价格（open/high/low/close） | 40,027 | 2.8 MB |
| `stock_daily_volamt_20261006_114938.csv` | 被改的 68,781 行原始量额（volume/amount） | 68,781 | 2.9 MB |
| `stock_daily_volamt_20261006_115305.csv` | 被改的 68,781 行原始量额（volume/amount） | 68,781 | 2.9 MB |

---

## 三、修复内容摘要

| 序 | 问题 | 规模 | 判据 | 验证结果 |
|---|---|---|---|---|
| 1 | 2026-07-06 单日污染 | 129 行 | 错值既不等于邻日也不等于同花顺 | 差异归零（1 只复权股被护栏排除） |
| 2 | 复权口径错误 | 149 只 / 40,027 行 | 比值分段恒定=复权因子特征 | 价格列差异归零 |
| 3 | volume/amount 单位错乱 | 68,781 行 | **成交额÷成交量=均价须落在当日[最低,最高]内**：同花顺 100.000%，004 仅 99.170% | 均价越界 0、volume 不符 0 |

> 另：236,860 行**仅 amount 小差异**（中位 0.76%、随机波动、均价正常）
> 判定为厂商舍入差异，**刻意未修**。详见《同花顺接口能力实测报告_20261006.md》§4.2d。

---

## 四、占用

- 整库副本 3 份 × 1.8GB = 5.2 GB
- 逐行 CSV 6 份 = 11.4 MB
- **合计 5.25 GB**（磁盘可用 43TB，占比约 0.01%）

**保留策略（2026-10-06 用户决定：先留着）**：
- 3 次修复刚完成，尚未经过实际使用检验 → 大备份先留着当退路
- 日后若确认数据无误，可删最早的 2 份 `.db.bak`（省 3.6G），保留最新 1 份
- **CSV 建议长期保留**（仅 6MB，且是精确回滚依据）

#!/usr/bin/env python3
"""生成 004 数据修复的备份清单（data/backup/MANIFEST.md）。

背景：023_THS 对 004 的 stock_daily 做了 3 次修复，每次动手前都留了备份。
本脚本扫描 data/backup/，把「哪份备份对应哪次修复、怎么回滚」写成文档，
避免日后忘记或误用。

★ 关键提醒（会写进清单）：这些 .db.bak 是**修复前**的快照，
  里面装的仍是**错误数据**——它们是「退路」，不是「干净备份」。

用法：
  python scripts/gen_backup_manifest.py
"""
import hashlib
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BK = os.path.join(ROOT, "data", "backup")
DB = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x/data/sequoia_v2.db"

# 时间戳 → 修复内容（与三次修复脚本对应）
FIXES = {
    "094130": ("2026-07-06 单日污染", "129 行",
               "scripts/fix_20260706_corruption.py",
               "92 只 002xxx + 37 只 300xxx，值与邻日、同花顺均不符"),
    "103622": ("复权口径错误", "40,027 行价格",
               "scripts/fix_adjusted_rows.py",
               "149 只，区间 2024-01-02~2026-06-08，仅价格 4 列（量额未动）"),
    "115305": ("volume/amount 单位错乱", "68,781 行",
               "scripts/fix_volume_amount.py",
               "99.4% 集中于 2026；均价越界 62,835 + volume 不符 5,946"),
}


def md5(path, chunk=1 << 22):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main():
    if not os.path.isdir(BK):
        raise SystemExit(f"❌ 备份目录不存在: {BK}")

    dbs, csvs = [], []
    for fn in sorted(os.listdir(BK)):
        p = os.path.join(BK, fn)
        if not os.path.isfile(p):
            continue
        rec = {"name": fn, "size": os.path.getsize(p),
               "mtime": datetime.fromtimestamp(os.path.getmtime(p))}
        (dbs if fn.endswith(".db.bak") else csvs).append(rec)

    total = sum(r["size"] for r in dbs + csvs)

    L = []
    L.append("# 004 数据修复 · 备份清单")
    L.append("")
    L.append(f"> 自动生成：{datetime.now():%Y-%m-%d %H:%M:%S} ｜ "
             f"生成器：`scripts/gen_backup_manifest.py`")
    L.append("> 目录：`023_THS/data/backup/`（已 gitignore，不入库）")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## ⚠️ 使用前必读（重要）")
    L.append("")
    L.append("**这些 `.db.bak` 是「修复前」的快照——里面装的仍然是错误数据。**")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append("| ✅ **可以做的事** | 如果发现修复改错了，用它覆盖回去，**退回修复前**的状态 |")
    L.append("| ❌ **不能做的事** | 把它当作「干净备份」来恢复/参考——它不是干净版本，是**事故现场照片** |")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 一、整库副本（`.db.bak`）——「整栋楼拍照」")
    L.append("")
    L.append("每次修复前复制整个数据库（1.8GB）。要回滚时，把它覆盖回生产库路径即可**整体退回**。")
    L.append("")
    L.append("| 备份文件 | 对应修复 | 规模 | 修复脚本 | 大小 |")
    L.append("|---|---|---|---|---|")
    for r in dbs:
        ts = r["name"].split("_")[-1].replace(".db.bak", "")
        info = FIXES.get(ts)
        if info:
            what, scale, script, _ = info
            L.append(f"| `{r['name']}` | {what} | {scale} | `{script}` | "
                     f"{r['size']/1024**3:.2f} GB |")
        else:
            L.append(f"| `{r['name']}` | （未登记） | — | — | {r['size']/1024**3:.2f} GB |")
    L.append("")
    L.append("**回滚方法**（以退回第二次修复前为例）：")
    L.append("")
    L.append("```bash")
    L.append("# 1. 先停掉所有读写 004 库的进程（流水线/回测）")
    L.append("# 2. 把当前库另存一份（万一还想再回来）")
    L.append(f"cp {DB} {DB}.before_rollback")
    L.append("# 3. 用备份覆盖")
    L.append(f"cp {os.path.join(BK, 'sequoia_v2_20261006_103622.db.bak')} {DB}")
    L.append("```")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 二、逐行快照（`.csv`）——「只拍动过的那几块砖」")
    L.append("")
    L.append("只导出**被修改的行**的原始值。回滚更精准：只改回那几行，不碰其他 750 万行。")
    L.append("")
    L.append("| CSV 文件 | 内容 | 行数 | 大小 |")
    L.append("|---|---|---|---|")
    hints = {"20260706": "2026-07-06 被改的 129 行原始值",
             "price": "被改的 40,027 行原始价格（open/high/low/close）",
             "volamt": "被改的 68,781 行原始量额（volume/amount）"}
    for r in csvs:
        hint = next((v for k, v in hints.items() if k in r["name"]), "—")
        n = sum(1 for _ in open(os.path.join(BK, r["name"])))
        L.append(f"| `{r['name']}` | {hint} | {n-1:,} | {r['size']/1024**2:.1f} MB |")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 三、修复内容摘要")
    L.append("")
    L.append("| 序 | 问题 | 规模 | 判据 | 验证结果 |")
    L.append("|---|---|---|---|---|")
    L.append("| 1 | 2026-07-06 单日污染 | 129 行 | 错值既不等于邻日也不等于同花顺 | 差异归零（1 只复权股被护栏排除） |")
    L.append("| 2 | 复权口径错误 | 149 只 / 40,027 行 | 比值分段恒定=复权因子特征 | 价格列差异归零 |")
    L.append("| 3 | volume/amount 单位错乱 | 68,781 行 | **成交额÷成交量=均价须落在当日[最低,最高]内**：同花顺 100.000%，004 仅 99.170% | 均价越界 0、volume 不符 0 |")
    L.append("")
    L.append("> 另：236,860 行**仅 amount 小差异**（中位 0.76%、随机波动、均价正常）")
    L.append("> 判定为厂商舍入差异，**刻意未修**。详见《同花顺接口能力实测报告_20261006.md》§4.2d。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 四、占用")
    L.append("")
    L.append(f"- 整库副本 {len(dbs)} 份 × 1.8GB = {sum(r['size'] for r in dbs)/1024**3:.1f} GB")
    L.append(f"- 逐行 CSV {len(csvs)} 份 = {sum(r['size'] for r in csvs)/1024**2:.1f} MB")
    L.append(f"- **合计 {total/1024**3:.2f} GB**（磁盘可用 43TB，占比约 0.01%）")
    L.append("")
    L.append("**保留策略（2026-10-06 用户决定：先留着）**：")
    L.append("- 3 次修复刚完成，尚未经过实际使用检验 → 大备份先留着当退路")
    L.append("- 日后若确认数据无误，可删最早的 2 份 `.db.bak`（省 3.6G），保留最新 1 份")
    L.append("- **CSV 建议长期保留**（仅 6MB，且是精确回滚依据）")
    L.append("")

    out = os.path.join(BK, "MANIFEST.md")
    open(out, "w").write("\n".join(L))
    print(f"✅ 已生成 {out}")
    print(f"   整库副本 {len(dbs)} 份 / 逐行CSV {len(csvs)} 份 / 合计 {total/1024**3:.2f} GB")


if __name__ == "__main__":
    main()

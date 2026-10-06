# 023_THS —— 同花顺金融数据 API 能力实测

> 建立日期：2026-10-06 ｜ 对标项目：`020_TDX`（通达信 MCP 验证）
> 目的：探询同花顺官方开源接口 `HiThink-Tech/Financial-API` **能免费拿到哪些数据、质量如何、
> 能否增补 004_sequoia-x 的非行情数据**

---

## 快速入口

| 我想看… | 打开 |
|---|---|
| **结论与证据** | [`reports/同花顺接口能力实测报告_20261006.md`](reports/同花顺接口能力实测报告_20261006.md) |
| **该怎么做** | [`reports/数据补齐方案总结_20261006.md`](reports/数据补齐方案总结_20261006.md) |
| 哪些端点能用 | `data/endpoint_availability.json` |
| 真实响应长什么样 | `sample_data/`（81 个 JSON） |

---

## 一句话结论

- ✅ **行情底座白送**：全市场十年日 K（1031 万行）+ 复权因子，Parquet 一键下载（173MB / 21 秒）
- ✅ **财务可增补**：三表（含研发费用、现金流）+ 23 指标，其中 **19 个 004 没有**
- ❌ **资金/新闻/股东/研报拿不到**（`code=2004` 客户端专用，或根本无该端点）
- ⚠️ **`report_date_ms` 系统性晚一年，不可用于防未来函数**

---

## 环境依赖

```bash
# API Key（已写入系统环境变量，值不落盘、不入库）
echo $HITHINK_FINANCE_API_KEY      # 应输出 sk-fuy 开头的密钥
# 如未配置：export HITHINK_FINANCE_API_KEY=<到 fuyao.aicubes.cn 后台签发>

# Python 环境（铁律：本项目用 py312）
/home/zhulei/anaconda3/envs/zhulei_py312/bin/python

# 依赖：requests / pandas / pyarrow / numpy（均已安装）
```

---

## 脚本用法

```bash
cd /public/home/hpc/zhulei/superman/quant/code/023_THS
PY=/home/zhulei/anaconda3/envs/zhulei_py312/bin/python

# ① 全端点可用性普查（支持断点续跑，结果落 data/probe_progress.json）
$PY scripts/probe_all_endpoints.py

# ② Parquet 全市场导出的质量验证（四层：结构/字段/勾稽/对拍）
$PY scripts/verify_dump_quality.py --sample 40

# ③ ★跨源逐值对拍：同花顺 vs 004 现有行情
$PY scripts/cross_check_ohlcv.py --sample 200 --days 120

# ④ ★用同花顺当标尺扫描 004 的数据异常
$PY scripts/scan_004_anomalies.py --start 2026-01-01

# ⑤ 财务数据对拍 + 增补分析
$PY scripts/compare_finance_ths_vs_004.py

# ⑥ 23 指标覆盖率 + 除权除息对拍
$PY scripts/compare_xdxr_and_coverage.py --n 60
```

---

## 数据资产

| 路径 | 内容 | 大小 |
|---|---|---|
| `data/dump/daily-k.parquet` | 全市场十年日 K（不复权） | 173 MB |
| `data/dump/daily-k-10d.parquet` | 最近 10 交易日全市场 | 1.0 MB |
| `data/dump/adjustment-factors.parquet` | 全市场复权因子 | 0.3 MB |
| `sample_data/` | 81 个端点的真实响应样本 | 1.5 MB |

> ⚠️ **dump 文件会过期**：预签名链接 5 分钟有效，但**下载到本地的 Parquet 是快照**。
> 需要最新数据时重新调 `GET /api/dump/market-dumps/{kind}/download-url`。
> 当前快照版本：`20261005`。

---

## 注意事项（踩坑记录）

1. **两套日期格式并存** —— `prices/historical` 的 `start/end` 要**毫秒时间戳**；
   `special-data` / `fund` 的日期要 **`yyyy-MM-dd` 字符串**。传错报 `code=1002`。
2. **`date_ms` 是北京时间零点** —— 用 pandas 直接 `unit="ms"` 转会**整体偏前一天**，
   必须先 `utc=True` 再 `tz_convert("Asia/Shanghai")`。（本项目的对拍脚本踩过这个坑）
3. **dump 是不复权的**（`adjusted=none`）—— 算收益率必须自己配 `adjustment-factors`。
4. **`report_date_ms` 不可信** —— 系统性晚一年，详见能力报告第 6.1 节。
5. **退市股拿不到** —— 同花顺只认在市标的（抽样失败率 ~22%，全是退市股）。
6. **限流** —— 官方声明不限累计次数，但需控制频率；触发返回 `HTTP 429` 或 `code=4001`，
   脚本已内置指数退避重试。

---

## 相关项目

- `020_TDX` —— 通达信 MCP 验证（本项目方法论的来源）
- `017_workbuddy/004_sequoia-x` —— 被增补目标；已有
  `THS_API_数据盘点与接入规划.md`（读文档版）、`THS_MOOTDX_MIGRATION_PLAN.md`

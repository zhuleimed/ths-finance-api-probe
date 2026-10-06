#!/usr/bin/env python3
"""Step A：采集同花顺财务三表 + 23 财务指标 → 004 的 finance_ths_v2 子集。

━━ 定位 ━━
**只写新目录，不碰 004 的现有特征（129 维）与 feature_version**，因此不触发
任何缓存重建，可随时运行（见 023_THS/reports/数据补齐方案总结_20261006.md §7.1）。

━━ 采集内容 ━━
    income/     利润表     季度，range 分段（每段≤10年）覆盖 2000→今
    balance/    资产负债表 同上
    cashflow/   现金流量表 同上
    indicators/ 23 财务指标 逐报告期（2012Q1→今），上市前的季度自动跳过

━━ 铁律合规（004 项目 CLAUDE.md）━━
  1. 断点续跑：每只完成即落盘；启动时扫描已有文件自动跳过
  2. 详尽日志：阶段 + 进度% + ETA + 速率 + 每只的自检结果
  3. 失败留痕：failed_{subset}.txt，下次自动重试
  4. 运行时自检：每只采完校验（行数>0、关键列存在、数值可解析）
  5. 限流保护：遇 4001/429 指数退避重试

用法：
  HITHINK_FINANCE_API_KEY=<key> python collect_finance_ths_v2.py --subsets statements
  HITHINK_FINANCE_API_KEY=<key> python collect_finance_ths_v2.py --subsets indicators
  # 同命令重跑即续跑；--limit N 可小样本试跑
"""
import argparse
import json
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import requests

BASE = "https://fuyao.aicubes.cn"
KEY = os.environ.get("HITHINK_FINANCE_API_KEY", "")
SX = "/public/home/hpc/zhulei/superman/quant/code/017_workbuddy/004_sequoia-x"
OUT = os.path.join(SX, "data", "extra_features", "finance_ths_v2")
DB = os.path.join(SX, "data", "sequoia_v2.db")
CODES = os.path.join(SX, "scripts", "all_a_codes.txt")

STATEMENTS = {"income": "income-statements",
              "balance": "balance-sheets",
              "cashflow": "cash-flow-statements"}
# 三表 range 窗口上限 10 年 → 用 9 年一段，安全
YEAR_CHUNKS = [(2000, 2009), (2009, 2018), (2018, 2027)]
IND_START_YEAR = 2012          # 指标数据实测最早 2011（仅个别股），统一从 2012 起
IND_END_YEAR = 2026

_print_lock = threading.Lock()
_stat = {"ok": 0, "fail": 0, "rows": 0, "req": 0, "t0": time.time()}
_stat_lock = threading.Lock()

# ── 全局限速器：不管多少并发，总请求速率不超过 1/INTERVAL ──
_rl_lock = threading.Lock()
_rl_next = [0.0]
INTERVAL = 0.04


def _throttle():
    """确保任意两次请求之间至少间隔 INTERVAL 秒（全局，跨线程）。"""
    with _rl_lock:
        now = time.time()
        wait = _rl_next[0] - now
        if wait > 0:
            time.sleep(wait)
        _rl_next[0] = max(now, _rl_next[0]) + INTERVAL


def log(msg):
    with _print_lock:
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def thscode(c):
    if c.startswith(("60", "68", "90")):
        return c + ".SH"
    if c.startswith(("00", "30", "20")):
        return c + ".SZ"
    return c + ".BJ"


def req(path, params, retries=4):
    """带指数退避的请求；遇 4001/429 退避重试。"""
    for a in range(retries + 1):
        try:
            _throttle()
            r = requests.get(BASE + path, params=params,
                             headers={"X-api-key": KEY}, timeout=30)
            with _stat_lock:
                _stat["req"] += 1
            j = r.json()
            code = j.get("code")
            if code in (4001,) or r.status_code == 429:
                wait = 2 ** a * 1.5
                time.sleep(wait)
                continue
            return j
        except Exception as e:
            if a == retries:
                return {"code": "EXC", "message": str(e)[:120]}
            time.sleep(1.5 * (a + 1))
    return {"code": 4001, "message": "retries exhausted"}


def quarter_list(start_year, end_year):
    out = []
    for y in range(start_year, end_year + 1):
        for q in (1, 2, 3, 4):
            out.append(f"{y}-{q}")
    return out


def ms(y, m, d):
    import datetime as dt
    return int(dt.datetime(y, m, d).timestamp() * 1000)


def _rep_key(rep):
    """'2025-3' → (2025, 3)，用于比较报告期先后。"""
    y, q = rep.split("-")
    return (int(y), int(q))


def _rep_ge(a, b):
    return _rep_key(a) >= _rep_key(b)


def shift_report(rep, back):
    """把报告期往前推 back 个季度：('2026-2', 1) → '2026-1'"""
    y, q = _rep_key(rep)
    n = y * 4 + (q - 1) - back
    return f"{n // 4}-{n % 4 + 1}"


def merge_refresh(old, new, keys):
    """增量合并：新数据覆盖同键旧数据（财报会修订，新版为准）。"""
    if new is None or not len(new):
        return old
    if old is None or not len(old):
        return new
    # 列可能不同（新股/接口新增字段）→ outer 对齐
    for k in keys:
        if k not in old.columns:
            old[k] = None
        if k not in new.columns:
            new[k] = None
    df = pd.concat([old, new], ignore_index=True)
    return df.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)


# ── 采集：三表（全量：3 段 range 覆盖 2000→今）──
def fetch_statement(code, sub):
    frames = []
    for (y0, y1) in YEAR_CHUNKS:
        j = req(f"/api/a-share/financials/{STATEMENTS[sub]}",
                {"thscode": thscode(code), "period": "quarterly",
                 "start": ms(y0, 1, 1), "end": ms(y1, 12, 31)})
        if j.get("code") == 0:
            it = j.get("data", {}).get("item", [])
            if it:
                frames.append(pd.DataFrame(it))
        elif j.get("code") in (3001, 3002):     # 标的不存在/无数据
            break
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df["code"] = code
    df = df.drop_duplicates(subset=["fiscal_year", "fiscal_period"])
    return df


# ── 采集：三表（增量：最近 N 期，1 次调用）──
def fetch_statement_recent(code, sub, limit=6):
    j = req(f"/api/a-share/financials/{STATEMENTS[sub]}",
            {"thscode": thscode(code), "period": "quarterly", "limit": limit})
    if j.get("code") != 0:
        return None
    it = j.get("data", {}).get("item", [])
    if not it:
        return None
    df = pd.DataFrame(it)
    df["code"] = code
    return df.drop_duplicates(subset=["fiscal_year", "fiscal_period"])


# ── 采集：23 指标 ──
def fetch_indicators(code, start_year, min_report=None):
    """min_report 形如 '2025-3'；给了就只从该期往后取（增量模式）。"""
    reps = quarter_list(start_year, IND_END_YEAR)
    if min_report:
        reps = [r for r in reps if _rep_ge(r, min_report)]
    rows = []
    for rep in reps:
        j = req("/api/a-share/financials/indicators",
                {"thscode": thscode(code), "report": rep})
        if j.get("code") != 0:
            continue
        d = j.get("data", {})
        rec = {"code": code, "report": rep}
        n_nonnull = 0
        for ab in d.get("abilities", []):
            for ind in ab.get("indicators", []):
                v = ind.get("value")
                rec[ind["index_id"]] = v
                if v not in (None, "", "null"):
                    n_nonnull += 1
        if n_nonnull:
            rows.append(rec)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    # 数值列转 float，无法解析的置 NaN
    for c in df.columns:
        if c not in ("code", "report"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def self_check(df, sub):
    """"运行时自检：返回 (是否有效, 说明)"""
    if df is None or not len(df):
        return False, "空数据"
    if sub == "indicators":
        val_cols = [c for c in df.columns if c not in ("code", "report")]
        if len(val_cols) < 10:
            return False, f"指标列仅 {len(val_cols)} 个(<10)"
        if df[val_cols].notna().sum().sum() == 0:
            return False, "全部指标为空"
        return True, f"{len(df)}期 × {len(val_cols)}指标"
    for k in ("fiscal_year", "fiscal_period"):
        if k not in df.columns:
            return False, f"缺列 {k}"
    return True, f"{len(df)}期 × {len(df.columns)}字段"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subsets", default="statements",
                    choices=["statements", "indicators", "all"])
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--interval", type=float, default=0.04,
                    help="全局每请求最小间隔(秒)，决定总速率上限")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（试跑）")
    ap.add_argument("--ind-start", type=int, default=IND_START_YEAR)
    ap.add_argument("--refresh", action="store_true",
                    help="增量模式：只拉比已有数据更新的报告期，合并回写（月度同步用）")
    ap.add_argument("--refresh-periods", type=int, default=4,
                    help="增量时回看几期（防财报修订/漏采），默认 4")
    ap.add_argument("--out", default=None, help="覆盖输出目录（测试用沙箱）")
    args = ap.parse_args()

    global OUT
    if args.out:
        OUT = args.out

    if len(KEY) < 8:
        sys.exit("❌ 缺 HITHINK_FINANCE_API_KEY")
    subs = {"statements": list(STATEMENTS), "indicators": ["indicators"],
            "all": list(STATEMENTS) + ["indicators"]}[args.subsets]
    global INTERVAL
    INTERVAL = args.interval

    for s in subs:
        os.makedirs(os.path.join(OUT, s), exist_ok=True)

    # 股票清单 + 上市日期（用于跳过上市前的报告期）
    codes = [l.strip() for l in open(CODES) if l.strip()]
    if args.limit:
        codes = codes[:args.limit]
    conn = sqlite3.connect(DB)
    listed = dict(conn.execute("SELECT symbol, listed_date FROM stock_list").fetchall())
    conn.close()

    log("=" * 78)
    log(f"同花顺财务采集 → {OUT}")
    log(f"  模式={'★增量 REFRESH（只拉新报告期并合并）' if args.refresh else '全量'}  "
        f"子集={subs}  股票={len(codes)}  并发达={args.workers}")
    log(f"  间隔={args.interval}s  指标起始={args.ind_start}  "
        f"增量回看={args.refresh_periods}期")
    log(f"  全局速率上限≈{1/max(args.interval,0.001):.0f} req/s（受全局限速器约束）")
    log("=" * 78)

    for sub in subs:
        path_of = lambda c: os.path.join(OUT, sub, f"{c}.parquet")
        if args.refresh:
            # 增量：已有文件是「基准」，全部股票都要处理（无文件的走全量补齐）
            todo = list(codes)
            have = sum(1 for c in codes if os.path.exists(path_of(c)))
            log(f"\n【{sub}】增量模式：{have} 只有基准文件，"
                f"{len(codes)-have} 只缺失将走全量补齐")
        else:
            todo = [c for c in codes if not os.path.exists(path_of(c))]
            log(f"\n【{sub}】待采 {len(todo)} / {len(codes)} 只（其余已完成，跳过）")
        if not todo:
            continue

        failed = []
        t0 = time.time()
        done = [0]
        n_add = [0]

        def work(code):
            old = None
            if args.refresh and os.path.exists(path_of(code)):
                try:
                    old = pd.read_parquet(path_of(code))
                except Exception:
                    old = None

            def _do():
                if old is not None and len(old):
                    # ── 增量分支：只取更新的期 ──
                    if sub == "indicators":
                        mr = str(old["report"].max())
                        mr = shift_report(mr, args.refresh_periods - 1)
                        return fetch_indicators(code, args.ind_start, min_report=mr)
                    return fetch_statement_recent(
                        code, sub, limit=max(args.refresh_periods, 4))
                # ── 全量分支 ──
                if sub == "indicators":
                    st = args.ind_start
                    ld = listed.get(code)
                    if ld:
                        try:
                            st = max(st, int(str(ld)[:4]))
                        except Exception:
                            pass
                    return fetch_indicators(code, st)
                return fetch_statement(code, sub)

            new = _do()
            if old is not None and len(old):
                keys = ["report"] if sub == "indicators" else ["fiscal_year", "fiscal_period"]
                before = len(old)
                df = merge_refresh(old, new, keys)
                n_add[0] += max(0, len(df) - before)
                ok, note = self_check(df, sub)
                return code, df, ok, note + f"(+{len(df)-before}期)"
            ok, note = self_check(new, sub)
            return code, new, ok, note

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(work, c): c for c in todo}
            for fut in as_completed(futs):
                code = futs[fut]
                try:
                    code, df, ok, note = fut.result()
                except Exception as e:
                    failed.append(code)
                    log(f"   ✗ {code} 异常 {str(e)[:60]}")
                    continue
                if ok:
                    # ★原子写入：先写临时文件再改名。refresh 模式会覆盖已有文件，
                    # 直接写若中途崩溃会丢历史数据；os.replace 是原子操作，不会写坏。
                    fp_ = os.path.join(OUT, sub, f"{code}.parquet")
                    tmp = fp_ + ".tmp"
                    try:
                        df.to_parquet(tmp, index=False)
                        os.replace(tmp, fp_)
                    except Exception as e:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                        failed.append(code)
                        log(f"   ✗ {code} 写盘失败 {str(e)[:50]}")
                        continue
                    with _stat_lock:
                        _stat["ok"] += 1
                        _stat["rows"] += len(df)
                else:
                    failed.append(code)
                    log(f"   ✗ {code} 自检失败：{note}")
                done[0] += 1
                if done[0] % 100 == 0 or done[0] == len(todo):
                    el = time.time() - t0
                    rate = done[0] / el
                    eta = (len(todo) - done[0]) / rate if rate else 0
                    with _stat_lock:
                        rq = _stat["req"]
                    log(f"   [{sub}] {done[0]:>5}/{len(todo)} ({done[0]/len(todo)*100:5.1f}%)  "
                        f"耗时 {el/60:5.1f}min  {rate:.1f} 股/s  请求 {rq}  "
                        f"ETA {eta/60:5.1f}min")

        fp = os.path.join(OUT, f"failed_{sub}.txt")
        with open(fp, "w") as f:
            f.write("\n".join(failed))
        extra = f"，新增 {n_add[0]:,} 期" if args.refresh else ""
        log(f"【{sub}】完成：成功 {len(todo)-len(failed)} / {len(todo)}{extra}，"
            f"失败 {len(failed)} 只 → {fp}")

    with _stat_lock:
        log(f"\n总计：成功 {_stat['ok']} 只 / {_stat['rows']:,} 行 / "
            f"请求 {_stat['req']:,} 次 / 耗时 {(time.time()-_stat['t0'])/60:.1f}min")
    man = {"updated": str(datetime.now()), "out": OUT, "subsets": subs,
           "mode": "refresh" if args.refresh else "full",
           "n_codes": len(codes), "workers": args.workers,
           "ind_start": args.ind_start, **{k: _stat[k] for k in ("ok", "fail", "rows", "req")}}
    json.dump(man, open(os.path.join(OUT, "manifest.json"), "w"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

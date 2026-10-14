#!/usr/bin/env python3
"""Fetch ~2 年 60 分鐘 K 線（僅台股觀察名單），輸出長格式 CSV。

給 GoogleSheet.Banks/fugle_stock_advisor.py 的 volume_profile() 用，取代原本「只有日線
開高低收，假設當天成交量在高低區間內均勻分布」的近似做法——60 分鐘 K 線是 yfinance 免費
就能拿到、回溯最久（約 2 年）的 intraday 級距，每個交易日約 5 根棒（9:00~13:30），用它算
Volume Profile 不用再猜當天量怎麼分布，分辨率是「小時」而不是「逐筆」但已經是免費資料源
能拿到的最細粒度。跟 fetch_daily_price.py 的日線 pipeline分開維護、互不影響，因為兩者的
interval/period 限制與合併鍵（日期 vs. 完整時間戳）都不一樣。

只抓台股（volume_profile() 目前只用在停泊股效率排名，不含美股概念股），不像
fetch_daily_price.py 還要處理 ConceptStocks 美股清單。

增量抓取：已有資料的代號只抓「既有最後一筆時間戳 - 緩衝天數」到現在，跟舊資料依完整時間戳
合併（同一根棒以新抓的為準）後只保留最近 RETENTION_DAYS 天。全新代號整段 bootstrap
（yfinance 60m interval 最長只能回溯約 2 年，超過會被 API 自動截斷，不用自己另外限制）。

台股不預先假設上市（.TW）或上櫃（.TWO），兩個 suffix 都試，抓得到資料的那個就是對的。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import yfinance as yf


def _find_repo_root(start: Path) -> Path:
    """Walk up from this script looking for the consuming repo root
    (marked by configs/default.yaml), so this skill works whether it lives
    in the skills registry or is deployed into a consumer repo at any depth."""
    env = os.environ.get("YAHOO_FINANCE_REPO_ROOT")
    if env:
        return Path(env)
    for candidate in [start, *start.parents]:
        if (candidate / "configs" / "default.yaml").exists():
            return candidate
    return start


REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)

RAW_COLUMNS = [
    "stock_code", "company_name", "yahoo_symbol",
    "時間戳", "開盤價", "最高價", "最低價", "收盤價", "volume",
    "download_timestamp",
]

BOOTSTRAP_PERIOD = "730d"    # yfinance 60m interval 最長回溯範圍
INCREMENTAL_OVERLAP_DAYS = 3  # 從「既有最後一筆時間戳 - 這麼多天」開始重抓，蓋掉近期可能還沒收斂的棒
RETENTION_DAYS = 730

# 非台股的額外代號（原樣使用、不做 .TW/.TWO 解析）：NQ=F 的台北 08 時小時棒是
# GoogleSheet.Banks --pre-market 開盤前簡報「早上看期貨」的歷史對照資料。
EXTRA_SYMBOLS = [
    ("NQ=F", "那斯達克100期貨"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_csv_rows(path: Path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_tw_targets(path: Path):
    targets = []
    for row in read_csv_rows(path):
        code = (row.get("代號") or "").strip()
        name = (row.get("名稱") or "").strip()
        if code:
            targets.append({"stock_code": code, "company_name": name})
    return targets


def load_existing_by_code(path: Path):
    by_code = {}
    for row in read_csv_rows(path):
        by_code.setdefault(row.get("stock_code", ""), []).append(row)
    return by_code


def fetch_history(yahoo_symbol: str, start: str | None = None):
    try:
        if start:
            hist = yf.download(yahoo_symbol, start=start, interval="60m", progress=False, auto_adjust=True)
        else:
            hist = yf.download(yahoo_symbol, period=BOOTSTRAP_PERIOD, interval="60m", progress=False, auto_adjust=True)
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    return hist


def rows_from_history(stock_code, company_name, yahoo_symbol, hist):
    ts = utc_now()
    out = []
    for idx, row in hist.iterrows():
        def _f(col):
            v = row.get(col)
            try:
                return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
            except (TypeError, ValueError):
                return ""
        out.append({
            "stock_code": stock_code, "company_name": company_name,
            "yahoo_symbol": yahoo_symbol, "時間戳": idx.strftime("%Y-%m-%d %H:%M:%S%z"),
            "開盤價": _f("Open"), "最高價": _f("High"), "最低價": _f("Low"), "收盤價": _f("Close"),
            "volume": _f("Volume"), "download_timestamp": ts,
        })
    return out


def merge_rows(existing_rows, new_rows, retention_days):
    """existing + new 合併，同一根棒（時間戳）以 new 為準，依時間戳排序，
    只保留 retention_days 天內的資料。"""
    by_ts = {r["時間戳"]: r for r in existing_rows}
    for r in new_rows:
        by_ts[r["時間戳"]] = r
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    merged = [r for r in by_ts.values() if r["時間戳"][:10] >= cutoff]
    merged.sort(key=lambda r: r["時間戳"])
    return merged


def fetch_one(code, name, resolve_symbols, existing_by_code, sleep_sec):
    """resolve_symbols: 依序嘗試的 yahoo_symbol 清單（[code.TW, code.TWO]）。
    回傳 (merged_rows, resolved_symbol) 或 (None, None) 代表整段都抓失敗。"""
    existing = existing_by_code.get(code, [])
    last_ts = max((r["時間戳"] for r in existing), default=None)

    for symbol in resolve_symbols:
        if last_ts:
            start = (datetime.strptime(last_ts[:10], "%Y-%m-%d") - timedelta(days=INCREMENTAL_OVERLAP_DAYS)).strftime("%Y-%m-%d")
            hist = fetch_history(symbol, start=start)
        else:
            hist = fetch_history(symbol)  # 全新代號，整段 bootstrap
        if hist is not None:
            new_rows = rows_from_history(code, name, symbol, hist)
            merged = merge_rows(existing, new_rows, RETENTION_DAYS)
            return merged, symbol
        time.sleep(sleep_sec)
    return None, None


def fetch_tw(targets, existing_by_code, sleep_sec):
    all_rows, failed = [], []
    for t in targets:
        code = t["stock_code"]
        symbols = [f"{code}.TW", f"{code}.TWO"]
        merged, resolved = fetch_one(code, t["company_name"], symbols, existing_by_code, sleep_sec)
        if merged is not None:
            all_rows.extend(merged)
        else:
            failed.append(code)
            all_rows.extend(existing_by_code.get(code, []))
        print(f"  TW {code} {t['company_name']}: {'OK ' + resolved if resolved else 'FAILED'}")
        time.sleep(sleep_sec)
    return all_rows, failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tw-list", default=str(REPO_ROOT / "StockID_TWSE_TPEX.csv"))
    parser.add_argument("--output-csv", default=str(REPO_ROOT / "data" / "reports" / "raw_yahoo_finance_intraday_60m.csv"))
    parser.add_argument("--sleep-sec", type=float, default=0.3)
    parser.add_argument("--full-refresh", action="store_true",
                         help="忽略既有 CSV，全部代號整段重抓 bootstrap 期間（正常增量抓取失效或要重建基準線時用）")
    args = parser.parse_args()

    out_path = Path(args.output_csv)
    tw_targets = load_tw_targets(Path(args.tw_list))
    existing_by_code = {} if args.full_refresh else load_existing_by_code(out_path)
    print(f"TW targets: {len(tw_targets)}, 既有代號數: {len(existing_by_code)}"
          f"{'（忽略，全量重抓）' if args.full_refresh else ''}")

    print("== 抓取台股 60 分鐘 K 線 ==")
    tw_rows, tw_failed = fetch_tw(tw_targets, existing_by_code, args.sleep_sec)

    print("== 抓取額外代號（期貨等，原樣代號） ==")
    extra_rows = []
    for symbol, name in EXTRA_SYMBOLS:
        merged, resolved = fetch_one(symbol, name, [symbol], existing_by_code, args.sleep_sec)
        if merged is not None:
            extra_rows.extend(merged)
            print(f"  EXTRA {symbol} {name}: OK")
        else:
            tw_failed.append(symbol)
            extra_rows.extend(existing_by_code.get(symbol, []))
            print(f"  EXTRA {symbol} {name}: FAILED")

    all_rows = tw_rows + extra_rows
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RAW_COLUMNS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n寫入 {out_path}：{len(all_rows)} 筆（TW {len(tw_rows)}, EXTRA {len(extra_rows)}）")
    if tw_failed:
        print(f"這次抓取失敗（沿用舊資料）: {', '.join(tw_failed)}")


if __name__ == "__main__":
    main()

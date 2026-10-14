#!/usr/bin/env python3
"""Fetch Yahoo Finance analyst analysis into a long-format raw CSV."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yaml
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


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


REPO_ROOT = _find_repo_root(SCRIPT_DIR)

# Load environment variables from .env file
load_dotenv(REPO_ROOT / ".env")

from yahoo_client import YahooFinanceClient


RAW_COLUMNS = [
    "stock_code",
    "company_name",
    "market",
    "yahoo_symbol",
    "symbol_resolution_status",
    "concept_name",
    "source_url",
    "section",
    "source_method",
    "period",
    "metric",
    "metric_zh",
    "value",
    "unit",
    "currency",
    "fetch_status",
    "error_message",
    "source_metadata_timestamp",
    "download_timestamp",
    "process_timestamp",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config(path: str) -> Dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_csv_rows(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_tw_targets(path: str) -> List[Dict[str, str]]:
    targets = []
    for row in read_csv_rows(path):
        code = (row.get("代號") or "").strip()
        name = (row.get("名稱") or "").strip()
        if code:
            targets.append({"stock_code": code, "company_name": name, "market": "TW", "concept_name": "", "source_metadata_timestamp": ""})
    return targets


def load_us_targets(path: str) -> List[Dict[str, str]]:
    targets = []
    for row in read_csv_rows(path):
        ticker = (row.get("Ticker") or "").strip()
        name = (row.get("公司名稱") or "").strip()
        targets.append({
            "stock_code": ticker,
            "company_name": name,
            "market": "US",
            "concept_name": (row.get("概念欄位") or "").strip(),
            "source_metadata_timestamp": (row.get("process_timestamp") or "").strip(),
        })
    return targets


def base_row(target: Dict[str, str], yahoo_symbol: str, market: str, resolution: str, config: Dict, process_ts: str) -> Dict[str, str]:
    return {
        "stock_code": target["stock_code"],
        "company_name": target["company_name"],
        "market": market,
        "yahoo_symbol": yahoo_symbol,
        "symbol_resolution_status": resolution,
        "concept_name": target.get("concept_name", ""),
        "source_url": config["yahoo"]["source_url_template"].format(yahoo_symbol=yahoo_symbol) if yahoo_symbol else "",
        "section": "",
        "source_method": "",
        "period": "",
        "metric": "",
        "metric_zh": "",
        "value": "",
        "unit": "",
        "currency": "USD" if market == "US" else ("TWD" if market in {"TW", "TWO"} else ""),
        "fetch_status": "",
        "error_message": "",
        "source_metadata_timestamp": target.get("source_metadata_timestamp", ""),
        "download_timestamp": "",
        "process_timestamp": process_ts,
    }


def status_row(target: Dict[str, str], yahoo_symbol: str, market: str, resolution: str, status: str, message: str, config: Dict, process_ts: str) -> Dict[str, str]:
    row = base_row(target, yahoo_symbol, market, resolution, config, process_ts)
    row["fetch_status"] = status
    row["error_message"] = message
    row["download_timestamp"] = utc_now()
    return row


def fetch_symbol(client: YahooFinanceClient, target: Dict[str, str], yahoo_symbol: str, market: str, resolution: str, config: Dict, process_ts: str) -> List[Dict[str, str]]:
    try:
        tables = client.fetch_tables(yahoo_symbol)
        download_ts = utc_now()
        if not tables:
            return [status_row(target, yahoo_symbol, market, resolution, "empty", "Yahoo Finance analysis returned no tables", config, process_ts)]

        rows = []
        for table in tables:
            for item in client.flatten_table(table):
                row = base_row(target, yahoo_symbol, market, resolution, config, process_ts)
                row.update(item)
                row["fetch_status"] = "success"
                row["download_timestamp"] = download_ts
                rows.append(row)
        return rows or [status_row(target, yahoo_symbol, market, resolution, "empty", "Yahoo Finance analysis tables had no values", config, process_ts)]
    except Exception as exc:
        return [status_row(target, yahoo_symbol, market, resolution, "error", str(exc), config, process_ts)]


def fetch_tw(client: YahooFinanceClient, target: Dict[str, str], config: Dict, process_ts: str) -> List[Dict[str, str]]:
    code = target["stock_code"]
    if code == "0000":
        return [status_row(target, "", "TW", "skipped_index", "skipped_index", "Index symbol is skipped for Yahoo Finance analyst analysis", config, process_ts)]

    default_symbol = f"{code}{config['yahoo']['taiwan_default_suffix']}"
    rows = fetch_symbol(client, target, default_symbol, "TW", "resolved", config, process_ts)
    if any(row["fetch_status"] == "success" for row in rows):
        return rows

    fallback_symbol = f"{code}{config['yahoo']['taiwan_fallback_suffix']}"
    fallback_rows = fetch_symbol(client, target, fallback_symbol, "TWO", "fallback_to_two", config, process_ts)
    if any(row["fetch_status"] == "success" for row in fallback_rows):
        return fallback_rows

    for row in rows + fallback_rows:
        row["symbol_resolution_status"] = "not_found"
    return rows + fallback_rows


def fetch_us(client: YahooFinanceClient, target: Dict[str, str], config: Dict, process_ts: str) -> List[Dict[str, str]]:
    ticker = target["stock_code"]
    if not ticker or ticker == "-":
        return [status_row(target, ticker, "US", "skipped_private_or_missing_ticker", "skipped_private_or_missing_ticker", "Private company or missing ticker", config, process_ts)]
    return fetch_symbol(client, target, ticker, "US", "resolved", config, process_ts)


def filter_specific(targets: Iterable[Dict[str, str]], specific_symbols: str) -> List[Dict[str, str]]:
    symbols = {part.strip().upper() for part in specific_symbols.split(",") if part.strip()}
    if not symbols:
        return list(targets)
    return [target for target in targets if target["stock_code"].upper() in symbols]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--market", choices=["all", "tw", "us"], default="all")
    parser.add_argument("--tw-list", choices=["all", "focus"], default="all")
    parser.add_argument("--specific-symbols", default="")
    parser.add_argument("--output-csv")
    args = parser.parse_args()

    config = load_config(args.config)
    process_ts = utc_now()
    client = YahooFinanceClient()
    all_rows: List[Dict[str, str]] = []

    if args.market in {"all", "tw"}:
        list_path = config["input"]["tw_all_list"] if args.tw_list == "all" else config["input"]["tw_focus_list"]
        tw_targets = filter_specific(load_tw_targets(list_path), args.specific_symbols)
        print(f"Fetching Taiwan targets: {len(tw_targets)} from {list_path}")
        for target in tw_targets:
            all_rows.extend(fetch_tw(client, target, config, process_ts))
            time.sleep(float(config["yahoo"].get("rate_limit_seconds", 1.0)))

    if args.market in {"all", "us"}:
        us_targets = filter_specific(load_us_targets(config["input"]["us_metadata"]), args.specific_symbols)
        print(f"Fetching US targets: {len(us_targets)} from {config['input']['us_metadata']}")
        for target in us_targets:
            all_rows.extend(fetch_us(client, target, config, process_ts))
            time.sleep(float(config["yahoo"].get("rate_limit_seconds", 1.0)))

    output_path = Path(args.output_csv or config["output"]["raw_csv"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RAW_COLUMNS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Wrote {len(all_rows)} rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

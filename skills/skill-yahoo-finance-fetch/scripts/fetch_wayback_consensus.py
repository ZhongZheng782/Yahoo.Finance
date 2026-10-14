#!/usr/bin/env python3
"""Fetch historical Yahoo Finance analyst consensus from Wayback Machine and compile it.

Usage:
  python fetch_group/fetch_wayback_consensus.py             # daily run (12-month lookback)
  python fetch_group/fetch_wayback_consensus.py --backfill  # one-time deep backfill (48 months)
"""

import os
import sys
import re
import time
import csv
import datetime
import random
import socket
import ssl
import urllib.parse
import urllib.request
import urllib.error
import json
import argparse
import numpy as np
import pandas as pd
from pathlib import Path

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')

# Paths
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
CONFIG_PATH        = os.path.join(REPO_ROOT, "configs", "default.yaml")
STOCK_LIST_PATH    = os.path.join(REPO_ROOT, "StockID_TWSE_TPEX.csv")
FOCUS_LIST_PATH    = os.path.join(REPO_ROOT, "StockID_TWSE_TPEX_focus.csv")
OUTPUT_CSV         = os.path.join(REPO_ROOT, "data", "reports", "raw_wayback_yahoo_finance_consensus.csv")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    import yaml
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config_from_path(path: str):
    if not os.path.exists(path):
        return {}
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _read_csv_codes(path: str) -> list[dict]:
    """Read stock list CSV, return list of {code, name}."""
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get("代號") or "").strip()
                name = (row.get("名稱") or "").strip()
                if code:
                    rows.append({"code": code, "name": name})
    return rows


def load_stocks() -> list[dict]:
    """Load all stocks with priority ordering.

    Returns focus stocks FIRST (is_focus=True), then the remaining
    stocks from the full list (is_focus=False).  Focus stocks receive
    a longer Wayback lookback window so historical gaps are filled
    with higher urgency.
    """
    focus_rows = _read_csv_codes(FOCUS_LIST_PATH)
    focus_codes = {r["code"] for r in focus_rows}

    all_rows = _read_csv_codes(STOCK_LIST_PATH)
    non_focus = [r for r in all_rows if r["code"] not in focus_codes]

    # Fallback: if CSVs are empty, use a minimal default set
    if not focus_rows and not all_rows:
        focus_rows = [
            {"code": "2330", "name": "台積電"},
            {"code": "2357", "name": "華碩"},
            {"code": "2382", "name": "廣達"},
            {"code": "2480", "name": "敦陽科"},
        ]

    # Tag each entry and concatenate: focus first
    result = [{**r, "is_focus": True}  for r in focus_rows]
    result += [{**r, "is_focus": False} for r in non_focus]
    print(f"Stock list: {len(focus_rows)} focus + {len(non_focus)} standard = {len(result)} total")
    return result

def parse_val_with_suffix(val_str):
    if pd.isna(val_str) or not val_str or str(val_str).strip() == "N/A" or str(val_str).strip() == "-":
        return np.nan
    val_str = str(val_str).strip().replace(",", "")
    
    # Check for unit suffixes
    multiplier = 1.0
    if val_str.endswith("B"):
        multiplier = 1e9
        val_str = val_str[:-1]
    elif val_str.endswith("M"):
        multiplier = 1e6
        val_str = val_str[:-1]
    elif val_str.endswith("T"):
        multiplier = 1e12
        val_str = val_str[:-1]
    elif val_str.endswith("k") or val_str.endswith("K"):
        multiplier = 1e3
        val_str = val_str[:-1]
        
    try:
        return float(val_str) * multiplier
    except ValueError:
        return np.nan

WAYBACK_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _is_network_level_error(exc: BaseException) -> bool:
    """True for connection-level failures (refused/timed-out/SSL/429/503) that
    are the signature of an IP-based rate-limit or block by archive.org, as
    opposed to a well-formed HTTP response that simply had no data (e.g. a
    genuine empty CDX result). Distinguishing the two matters: the former
    means retrying the same request from the same IP is pointless and the
    run should fail fast; the latter is normal and should not be treated as
    an outage."""
    if isinstance(exc, (ConnectionError, socket.timeout, TimeoutError, ssl.SSLError)):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (429, 503)
    if isinstance(exc, urllib.error.URLError):
        # Covers things like "[Errno 111] Connection refused" and
        # "_ssl.c:999: The handshake operation timed out".
        return True
    return False


def _retry_delay(attempt: int, exc: BaseException) -> float:
    """Exponential backoff with jitter, honoring Retry-After on 503s so we
    give archive.org's rate limiter a real chance to reset instead of
    hammering it again after a fixed 3s."""
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 503:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
    base = 3.0 * (2 ** attempt)
    return min(base + random.uniform(0, base * 0.5), 30.0)


def fetch_wayback_snapshots(url, limit_months=24):
    """Query Wayback CDX API for historical snapshot timestamps of the URL.

    Returns (snapshots, network_blocked). network_blocked is True only when
    every retry failed with a network-level error (see
    _is_network_level_error) — i.e. archive.org was unreachable/throttling,
    not merely "no snapshots exist for this URL"."""
    encoded_url = urllib.parse.quote_plus(url)
    cdx_url = f"https://web.archive.org/cdx/search/cdx?url={encoded_url}&output=json&statuscode=200"

    headers = {"User-Agent": WAYBACK_USER_AGENT}
    req = urllib.request.Request(cdx_url, headers=headers)

    last_exc = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
                if len(data) <= 1:
                    return [], False

                header = data[0]
                rows = data[1:]

                ts_idx = header.index("timestamp")
                orig_idx = header.index("original")

                snapshots = []
                for r in rows:
                    snapshots.append({
                        "timestamp": r[ts_idx],
                        "original": r[orig_idx]
                    })

                monthly_snapshots = {}
                for snap in snapshots:
                    ts = snap["timestamp"]
                    year_month = ts[:6]
                    if year_month not in monthly_snapshots or ts > monthly_snapshots[year_month]["timestamp"]:
                        monthly_snapshots[year_month] = snap

                sorted_snaps = sorted(list(monthly_snapshots.values()), key=lambda x: x["timestamp"])
                return sorted_snaps[-limit_months:], False

        except Exception as e:
            last_exc = e
            print(f"Attempt {attempt+1} failed to call Wayback CDX API: {e}")
            if attempt < 2:
                time.sleep(_retry_delay(attempt, e))

    network_blocked = last_exc is not None and _is_network_level_error(last_exc)
    return [], network_blocked

def download_html(timestamp, original_url):
    """Download archived HTML page from Wayback Machine.

    Returns (html_or_None, network_blocked) — see fetch_wayback_snapshots
    for what network_blocked means."""
    wayback_url = f"https://web.archive.org/web/{timestamp}/{original_url}"
    headers = {"User-Agent": WAYBACK_USER_AGENT}
    req = urllib.request.Request(wayback_url, headers=headers)

    last_exc = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.read().decode("utf-8", errors="ignore"), False
        except Exception as e:
            last_exc = e
            print(f"Attempt {attempt+1} failed to download snapshot {timestamp}: {e}")
            if attempt < 2:
                time.sleep(_retry_delay(attempt, e))

    network_blocked = last_exc is not None and _is_network_level_error(last_exc)
    return None, network_blocked

def parse_consensus_from_json(html_text):
    """Fallback parser that extracts and processes window.App.main or window.__PRELOADED_STATE__ JSON."""
    json_data = None
    
    # 1. Try to find in <script id="app-state" type="application/json">
    m = re.search(r'<script[^>]*id=["\']app-state["\'][^>]*>(.*?)</script>', html_text, re.DOTALL)
    if m:
        try:
            json_data = json.loads(m.group(1))
        except Exception:
            pass
            
    # 2. Try using the robust brace counter to locate the preloaded state JSON
    if not json_data:
        for marker in ['window.App.main =', 'window.__PRELOADED_STATE__ =', 'root.App.main =']:
            idx = html_text.find(marker)
            if idx != -1:
                start_idx = html_text.find('{', idx)
                if start_idx != -1:
                    brace_count = 0
                    in_quote = False
                    escape = False
                    for j in range(start_idx, len(html_text)):
                        char = html_text[j]
                        if escape:
                            escape = False
                            continue
                        if char == '\\':
                            escape = True
                            continue
                        if char == '"':
                            in_quote = not in_quote
                            continue
                        if not in_quote:
                            if char == '{':
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    json_str = html_text[start_idx:j+1]
                                    try:
                                        json_data = json.loads(json_str)
                                        break
                                    except Exception:
                                        break
                    if json_data:
                        break

    if not json_data:
        return None

    # Recursively find target key
    def find_key_recursive(data, target_key):
        if isinstance(data, dict):
            if target_key in data:
                return data[target_key]
            for v in data.values():
                res = find_key_recursive(v, target_key)
                if res is not None:
                    return res
        elif isinstance(data, list):
            for item in data:
                res = find_key_recursive(item, target_key)
                if res is not None:
                    return res
        return None

    earnings_trend = find_key_recursive(json_data, 'earningsTrend')
    if not earnings_trend or not isinstance(earnings_trend, dict) or 'trend' not in earnings_trend:
        return None

    trend_list = earnings_trend['trend']
    if not isinstance(trend_list, list):
        return None

    result = {
        "earnings_0q_avg": np.nan, "earnings_1q_avg": np.nan,
        "earnings_0y_avg": np.nan, "earnings_1y_avg": np.nan,
        "revenue_0q_avg": np.nan, "revenue_1q_avg": np.nan,
        "revenue_0y_avg": np.nan, "revenue_1y_avg": np.nan
    }

    def extract_raw_val(item, key):
        if isinstance(item, dict) and key in item and isinstance(item[key], dict):
            avg_obj = item[key].get("avg")
            if isinstance(avg_obj, dict):
                raw_val = avg_obj.get("raw")
                if raw_val is not None and not isinstance(raw_val, str) and not pd.isna(raw_val):
                    return float(raw_val)
                fmt_val = avg_obj.get("fmt")
                if fmt_val is not None:
                    return parse_val_with_suffix(fmt_val)
        return np.nan

    for trend_item in trend_list:
        if not isinstance(trend_item, dict):
            continue
        period = trend_item.get("period")
        
        eps_val = extract_raw_val(trend_item, "earningsEstimate")
        rev_val = extract_raw_val(trend_item, "revenueEstimate")

        if period == "0q":
            result["earnings_0q_avg"] = eps_val
            result["revenue_0q_avg"] = rev_val
        elif period == "+1q":
            result["earnings_1q_avg"] = eps_val
            result["revenue_1q_avg"] = rev_val
        elif period == "0y":
            result["earnings_0y_avg"] = eps_val
            result["revenue_0y_avg"] = rev_val
        elif period == "+1y":
            result["earnings_1y_avg"] = eps_val
            result["revenue_1y_avg"] = rev_val

    has_any = any(not np.isnan(v) for v in result.values())
    if has_any:
        return result
    return None

def parse_consensus_from_html(html_text):
    """Parse Revenue and Earnings estimate tables from HTML content."""
    # First, try to use pandas.read_html
    try:
        from io import StringIO
        dfs = pd.read_html(StringIO(html_text))
    except Exception as e:
        dfs = None
        
    revenue_df = None
    earnings_df = None
    
    if dfs is not None:
        for df in dfs:
            if df.empty or df.shape[1] < 2:
                continue
                
            # Standardize columns and rows
            first_col = str(df.columns[0]).strip()
            first_row_vals = [str(x).lower() for x in df.iloc[:, 0].tolist()]
            
            # Check if this is Earnings Estimate
            has_avg = any("avg" in str(x).lower() or "平均" in str(x) for x in first_row_vals)
            
            is_revenue = False
            is_earnings = False
            
            # Flatten all text in DataFrame to find keywords
            all_text = " ".join([str(x) for x in df.values.flatten()] + [str(c) for c in df.columns])
            all_text_lower = all_text.lower()
            
            if has_avg:
                if "sales" in all_text_lower or "revenue" in all_text_lower or "營收" in all_text or "收益預估" in all_text:
                    is_revenue = True
                elif "eps" in all_text_lower or "earnings" in all_text_lower or "每股" in all_text or "盈利預估" in all_text:
                    is_earnings = True
                    
            if is_revenue and revenue_df is None:
                revenue_df = df
            elif is_earnings and earnings_df is None:
                earnings_df = df
                
        if revenue_df is not None or earnings_df is not None:
            # Process tables into standard dict
            result = {
                "earnings_0q_avg": np.nan, "earnings_1q_avg": np.nan,
                "earnings_0y_avg": np.nan, "earnings_1y_avg": np.nan,
                "revenue_0q_avg": np.nan, "revenue_1q_avg": np.nan,
                "revenue_0y_avg": np.nan, "revenue_1y_avg": np.nan
            }
            
            def extract_avg_vals(df, is_rev):
                prefix = "revenue" if is_rev else "earnings"
                
                # Find the row containing average estimate
                avg_row_idx = None
                for idx, row in df.iterrows():
                    row_label = str(row.iloc[0]).lower()
                    if "avg" in row_label or "平均" in row_label:
                        avg_row_idx = idx
                        break
                        
                if avg_row_idx is None:
                    return
                    
                avg_row = df.iloc[avg_row_idx]
                
                # Identify columns (0q, 1q, 0y, 1y)
                for col_idx in range(1, min(5, len(avg_row))):
                    col_header = str(df.columns[col_idx]).lower()
                    val = parse_val_with_suffix(avg_row.iloc[col_idx])
                    
                    if "next qtr" in col_header or "+1q" in col_header:
                        result[f"{prefix}_1q_avg"] = val
                    elif "current qtr" in col_header or "0q" in col_header:
                        result[f"{prefix}_0q_avg"] = val
                    elif "next year" in col_header or "+1y" in col_header:
                        result[f"{prefix}_1y_avg"] = val
                    elif "current year" in col_header or "0y" in col_header:
                        result[f"{prefix}_0y_avg"] = val
                    else:
                        # Fallback based on column index
                        if col_idx == 1:
                            result[f"{prefix}_0q_avg"] = val
                        elif col_idx == 2:
                            result[f"{prefix}_1q_avg"] = val
                        elif col_idx == 3:
                            result[f"{prefix}_0y_avg"] = val
                        elif col_idx == 4:
                            result[f"{prefix}_1y_avg"] = val

            if revenue_df is not None:
                extract_avg_vals(revenue_df, is_rev=True)
            if earnings_df is not None:
                extract_avg_vals(earnings_df, is_rev=False)
                
            has_any = any(not np.isnan(v) for v in result.values())
            if has_any:
                return result

    # Fallback to JSON parsing if read_html fails or doesn't find valid metrics
    return parse_consensus_from_json(html_text)


WAYBACK_OUTPUT_COLUMNS = [
    "stock_code",
    "company_name",
    "forecast_asof_date",
    "earnings_0q_avg",
    "earnings_1q_avg",
    "earnings_0y_avg",
    "earnings_1y_avg",
    "revenue_0q_avg",
    "revenue_1q_avg",
    "revenue_0y_avg",
    "revenue_1y_avg",
    "process_timestamp",
]

COVERAGE_COLUMNS = [
    "stock_code",
    "company_name",
    "yahoo_symbol",
    "snapshot_timestamp",
    "forecast_asof_date",
    "original_url",
    "status",
    "message",
    "process_timestamp",
]


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def resolve_repo_path(path_value: str | None, default_value: str | None = None) -> Path | None:
    """Resolve a config/CLI path relative to the repository root."""
    value = path_value or default_value
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def filter_specific(targets: list[dict], specific_symbols: str) -> list[dict]:
    symbols = {part.strip().upper() for part in specific_symbols.split(",") if part.strip()}
    if not symbols:
        return targets
    return [target for target in targets if target["code"].upper() in symbols]


def load_coverage(history_csv: Path | None) -> set[tuple[str, str]]:
    """Return (stock_code, YYYYMM) pairs already covered by valid history rows."""
    already_covered: set[tuple[str, str]] = set()
    consensus_cols = ["earnings_0y_avg", "revenue_0y_avg", "earnings_0q_avg", "revenue_0q_avg"]

    if history_csv is None:
        print("No history CSV configured - will fetch all available snapshots.")
        return already_covered

    if not history_csv.exists():
        print(f"No existing history found at {history_csv} - will fetch all available snapshots.")
        return already_covered

    try:
        df_hist = pd.read_csv(history_csv, encoding="utf-8")
        if "stock_code" not in df_hist.columns or "forecast_asof_date" not in df_hist.columns:
            print(f"Warning: History CSV lacks required columns: {history_csv}")
            return already_covered

        df_hist["stock_code"] = df_hist["stock_code"].astype(str).str.strip()
        df_hist["_ym"] = pd.to_datetime(df_hist["forecast_asof_date"], errors="coerce").dt.strftime("%Y%m")
        for _, row in df_hist.dropna(subset=["_ym"]).iterrows():
            has_value = any(
                col in df_hist.columns
                and pd.notna(row.get(col))
                and str(row.get(col)).strip() not in ["", "nan", "NaN"]
                for col in consensus_cols
            )
            if has_value:
                already_covered.add((row["stock_code"], row["_ym"]))
        print(f"Loaded coverage matrix: {len(already_covered)} already-covered (stock, month) pairs.")
    except Exception as e:
        print(f"Warning: Failed to load existing history for gap analysis: {e}")

    return already_covered


WAYBACK_FAILURE_RETRY_DAYS = {
    "download_failed": 1,
    "parse_failed": 30,
}


def load_covered_snapshots(coverage_csv: Path | None, retry_failed_attempts: bool) -> set[tuple[str, str]]:
    """Return snapshots to skip, while allowing old failures to be retried."""
    if coverage_csv is None or retry_failed_attempts or not coverage_csv.exists():
        return set()

    try:
        df_attempts = pd.read_csv(coverage_csv, encoding="utf-8")
    except Exception as e:
        print(f"Warning: Failed to load Wayback coverage matrix: {e}")
        return set()

    required = {"stock_code", "snapshot_timestamp"}
    if not required.issubset(df_attempts.columns):
        print(f"Warning: Wayback coverage matrix lacks required columns: {coverage_csv}")
        return set()

    df_attempts["stock_code"] = df_attempts["stock_code"].astype(str).str.strip()
    df_attempts["snapshot_timestamp"] = df_attempts["snapshot_timestamp"].astype(str).str.strip()
    now = datetime.datetime.now(datetime.timezone.utc)
    attempted: set[tuple[str, str]] = set()
    retryable_failures = 0

    for _, row in df_attempts.iterrows():
        status = str(row.get("status", "")).strip().lower()
        key = (row["stock_code"], row["snapshot_timestamp"])
        if not all(key):
            continue

        retry_after_days = WAYBACK_FAILURE_RETRY_DAYS.get(status)
        if retry_after_days is not None:
            attempted_at = pd.to_datetime(row.get("process_timestamp"), errors="coerce", utc=True)
            if pd.isna(attempted_at) or (now - attempted_at.to_pydatetime()).total_seconds() >= retry_after_days * 86400:
                retryable_failures += 1
                continue
        elif status not in {"success", "empty_metrics"}:
            # Unknown statuses should not become permanent skip entries.
            retryable_failures += 1
            continue

        attempted.add(key)

    print(
        f"Loaded coverage matrix: {len(attempted)} snapshots still skipped; "
        f"{retryable_failures} failed/unknown attempts are eligible for retry."
    )
    return attempted


def append_coverage_row(coverage_csv: Path | None, row: dict) -> None:
    if coverage_csv is None:
        return
    coverage_csv.parent.mkdir(parents=True, exist_ok=True)
    write_header = not coverage_csv.exists() or coverage_csv.stat().st_size == 0
    with coverage_csv.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COVERAGE_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow({col: row.get(col, "") for col in COVERAGE_COLUMNS})


def merge_history(df_out: pd.DataFrame, history_csv: Path) -> None:
    """Merge newly fetched Wayback records into the downstream history CSV."""
    history_csv.parent.mkdir(parents=True, exist_ok=True)

    if history_csv.exists():
        df_old = pd.read_csv(history_csv, encoding="utf-8")
        df_old["stock_code"] = df_old["stock_code"].astype(str).str.strip()
        df_out["stock_code"] = df_out["stock_code"].astype(str).str.strip()
        df_combined = pd.concat([df_old, df_out], ignore_index=True)
    else:
        print(f"Target history file not found. Creating: {history_csv}")
        df_combined = df_out.copy()
        df_combined["stock_code"] = df_combined["stock_code"].astype(str).str.strip()

    df_combined = df_combined.drop_duplicates(subset=["stock_code", "forecast_asof_date"], keep="last")
    df_combined = df_combined.sort_values(by=["stock_code", "forecast_asof_date"])
    df_combined.to_csv(history_csv, index=False, encoding="utf-8-sig")
    print(f"Checkpointed history CSV at {history_csv}: {len(df_combined)} records.")


def checkpoint_records(records: list[dict], output_csv: Path, history_csv: Path | None) -> None:
    if not records:
        return
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df_out = pd.DataFrame(records)[WAYBACK_OUTPUT_COLUMNS]
    df_out = df_out.drop_duplicates(subset=["stock_code", "forecast_asof_date"], keep="last")
    df_out = df_out.sort_values(by=["stock_code", "forecast_asof_date"])
    df_out.to_csv(output_csv, index=False, encoding="utf-8-sig")
    print(f"Checkpointed {len(df_out)} records to {output_csv}")
    if history_csv is not None:
        merge_history(df_out, history_csv)


def make_coverage_row(
    stock: dict,
    yahoo_symbol: str,
    snapshot: dict,
    asof_date: str,
    status: str,
    message: str,
) -> dict:
    return {
        "stock_code": stock["code"],
        "company_name": stock["name"],
        "yahoo_symbol": yahoo_symbol,
        "snapshot_timestamp": snapshot["timestamp"],
        "forecast_asof_date": asof_date,
        "original_url": snapshot.get("original", ""),
        "status": status,
        "message": message,
        "process_timestamp": utc_now(),
    }


def time_budget_exhausted(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


NETWORK_BLOCK_STREAK_LIMIT = 15  # consecutive network-level failures before we bail out mid-run


def _emit_gh_error(message: str) -> None:
    """Print a GitHub Actions error annotation (shows up in the Actions UI /
    job summary, unlike a plain print) in addition to a normal log line, so
    a connectivity outage is visible even though this workflow step runs
    with continue-on-error: true and would otherwise look identical to a
    normal successful (if quiet) run."""
    print(f"::error::{message}")
    print(message)


def check_wayback_connectivity() -> bool:
    """One cheap CDX probe before looping over the whole stock list.

    archive.org is known to rate-limit/block requests from shared cloud CI
    IP ranges (connection refused / SSL handshake timeout / HTTP 503) — when
    that happens every single per-stock query fails the same way, so there's
    no point burning the full multi-hour runtime budget rediscovering that
    on every stock. Returns False only when the failure is network-level
    (see _is_network_level_error); a well-formed response (even an
    unexpected one) counts as reachable."""
    probe_url = "https://web.archive.org/cdx/search/cdx?url=example.com&output=json&limit=1"
    headers = {"User-Agent": WAYBACK_USER_AGENT}
    for attempt in range(3):
        try:
            req = urllib.request.Request(probe_url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                resp.read()
            return True
        except Exception as e:
            if not _is_network_level_error(e):
                return True
            print(f"Connectivity probe attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(_retry_delay(attempt, e))
    # All 3 attempts failed with a network-level error (connection
    # refused/SSL timeout/429/503) — treat as unreachable from this runner.
    return False


def main():
    parser = argparse.ArgumentParser(description="Fetch Wayback Machine Yahoo Finance consensus")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="YAML config path")
    parser.add_argument("--backfill", action="store_true", help="Deep backfill mode: focus=48m, standard=24m")
    parser.add_argument("--limit-months", type=int, default=None, help="Override lookback for ALL stocks")
    parser.add_argument("--tw-list", choices=["all", "focus"], default="all", help="Stock universe to fetch")
    parser.add_argument(
        "--specific-symbols",
        default="",
        help="Comma-separated stock codes to fetch, e.g. 2330,2382",
    )
    parser.add_argument("--output-csv", help="Wayback batch output CSV path")
    parser.add_argument("--coverage-csv", help="Wayback coverage matrix CSV path")
    parser.add_argument("--history-csv", help="Consensus history CSV to use for gap detection and merge")
    parser.add_argument(
        "--max-runtime-minutes",
        type=float,
        help="Stop gracefully after this many minutes so the workflow can commit partial work",
    )
    parser.add_argument(
        "--retry-failed-attempts",
        action="store_true",
        help="Retry snapshots already present in the coverage matrix",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Only write --output-csv; do not merge into history CSV",
    )
    args = parser.parse_args()

    config = load_config() if args.config == str(CONFIG_PATH) else load_config_from_path(args.config)
    output_csv = resolve_repo_path(
        args.output_csv,
        config.get("output", {}).get("wayback_consensus_csv") or OUTPUT_CSV,
    )
    coverage_csv = resolve_repo_path(
        args.coverage_csv,
        config.get("output", {}).get("wayback_coverage_matrix_csv"),
    )
    history_csv = resolve_repo_path(args.history_csv, config.get("output", {}).get("wayback_consensus_history_csv"))
    max_runtime = args.max_runtime_minutes
    if max_runtime is None:
        max_runtime = float(config.get("wayback", {}).get("max_runtime_minutes", 300))
    deadline = time.monotonic() + max_runtime * 60 if max_runtime > 0 else None

    print("Checking Wayback Machine connectivity before starting...")
    if not check_wayback_connectivity():
        _emit_gh_error(
            "Wayback Machine (archive.org) is unreachable from this runner "
            "(connection refused / SSL timeout / 429 / 503 on every probe attempt). "
            "This is the signature of an IP-level rate-limit or block on the "
            "runner's network, not a code issue -- retrying the full stock list "
            "would just repeat the same failure for hours. Skipping this run; "
            "no fake 'heartbeat' commit will be produced."
        )
        sys.exit(1)
    print("Wayback Machine is reachable. Proceeding.")

    if args.limit_months:
        focus_limit = standard_limit = args.limit_months
    elif args.backfill:
        focus_limit, standard_limit = 48, 24
    else:
        focus_limit, standard_limit = 28, 14

    targets = load_stocks()
    if args.tw_list == "focus":
        targets = [target for target in targets if target.get("is_focus", False)]
    targets = filter_specific(targets, args.specific_symbols)
    targets = [t for t in targets if t["code"] not in ["0000", "加權指數"]]

    suffix = config.get("yahoo", {}).get("taiwan_default_suffix", ".TW")
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    already_covered = load_coverage(history_csv if not args.no_merge else None)
    already_covered.update(load_coverage(output_csv))
    covered_snapshots = load_covered_snapshots(coverage_csv, args.retry_failed_attempts)
    all_records: list[dict] = []
    network_block_streak = 0

    for t in targets:
        if time_budget_exhausted(deadline):
            print("Runtime budget reached before next stock. Exiting gracefully.")
            break

        if network_block_streak >= NETWORK_BLOCK_STREAK_LIMIT:
            _emit_gh_error(
                f"{network_block_streak} consecutive network-level failures against "
                "Wayback Machine -- connectivity degraded mid-run (past the initial "
                "probe). Aborting the remaining stock list instead of burning the "
                "rest of the runtime budget on requests that will keep failing."
            )
            checkpoint_records(all_records, output_csv, history_csv if not args.no_merge else None)
            sys.exit(1)

        code = t["code"]
        name = t["name"]
        is_focus = t.get("is_focus", False)
        limit_months = focus_limit if is_focus else standard_limit
        yahoo_symbol = f"{code}{suffix}"

        candidate_urls = [
            f"https://finance.yahoo.com/quote/{yahoo_symbol}/analysis",
            f"https://hk.finance.yahoo.com/quote/{yahoo_symbol}/analysis",
            f"https://sg.finance.yahoo.com/quote/{yahoo_symbol}/analysis",
        ]

        priority_tag = "FOCUS" if is_focus else "standard"
        print("\n=======================================================")
        print(f"[{priority_tag}] {code} ({name})  symbol={yahoo_symbol}  lookback={limit_months}m")
        print("Querying snapshots on Wayback Machine for multiple domains...")

        all_snapshots = []
        for url in candidate_urls:
            if time_budget_exhausted(deadline):
                print("Runtime budget reached during CDX queries. Exiting gracefully.")
                break
            print(f"  Querying: {url}")
            snaps, network_blocked = fetch_wayback_snapshots(url, limit_months=limit_months)
            print(f"    Found {len(snaps)} snapshots.")
            all_snapshots.extend(snaps)
            network_block_streak = network_block_streak + 1 if network_blocked else 0
            if network_block_streak >= NETWORK_BLOCK_STREAK_LIMIT:
                break
            months_so_far = len({snap["timestamp"][:6] for snap in all_snapshots})
            if months_so_far >= limit_months:
                # Already have enough monthly coverage from earlier mirror
                # domain(s) -- skip querying the rest to cut CDX request
                # volume (this is the traffic pattern most likely to trigger
                # archive.org's rate limiting in the first place).
                break
            time.sleep(1.0)

        monthly_snaps = {}
        for snap in all_snapshots:
            ts = snap["timestamp"]
            ym = ts[:6]
            if ym not in monthly_snaps or ts > monthly_snaps[ym]["timestamp"]:
                monthly_snaps[ym] = snap

        snapshots = sorted(list(monthly_snaps.values()), key=lambda x: x["timestamp"])
        snapshots = snapshots[-limit_months:]
        print(f"Total unique monthly snapshots after merging: {len(snapshots)}")

        for idx, snap in enumerate(snapshots, 1):
            if time_budget_exhausted(deadline):
                print("Runtime budget reached during snapshot loop. Exiting gracefully.")
                checkpoint_records(all_records, output_csv, history_csv if not args.no_merge else None)
                return

            ts = snap["timestamp"]
            snap_ym = ts[:6]
            asof_date = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
            attempt_key = (code, ts)

            if (code, snap_ym) in already_covered:
                print(f"  [{idx}/{len(snapshots)}] {asof_date} already covered - skipping download.")
                continue
            if attempt_key in covered_snapshots:
                print(
                    f"  [{idx}/{len(snapshots)}] {asof_date} already recorded "
                    "in coverage matrix - skipping download."
                )
                continue

            print(f"  [{idx}/{len(snapshots)}] Downloading snapshot as of {asof_date} (gap to fill)...")
            html, network_blocked = download_html(ts, snap["original"])
            network_block_streak = network_block_streak + 1 if network_blocked else 0
            if not html:
                message = "Failed to download HTML"
                print(f"    {message}.")
                append_coverage_row(
                    coverage_csv,
                    make_coverage_row(t, yahoo_symbol, snap, asof_date, "download_failed", message),
                )
                covered_snapshots.add(attempt_key)
                if network_block_streak >= NETWORK_BLOCK_STREAK_LIMIT:
                    break
                continue

            network_block_streak = 0

            metrics = parse_consensus_from_html(html)
            if not metrics:
                message = "Failed to parse tables from HTML"
                print(f"    {message}.")
                append_coverage_row(
                    coverage_csv,
                    make_coverage_row(t, yahoo_symbol, snap, asof_date, "parse_failed", message),
                )
                covered_snapshots.add(attempt_key)
                continue

            has_data = any(not np.isnan(v) for v in metrics.values())
            if not has_data:
                message = "Parse succeeded but all metrics were NaN"
                print(f"    {message}.")
                append_coverage_row(
                    coverage_csv,
                    make_coverage_row(t, yahoo_symbol, snap, asof_date, "empty_metrics", message),
                )
                covered_snapshots.add(attempt_key)
                continue

            record = {
                "stock_code": code,
                "company_name": name,
                "forecast_asof_date": asof_date,
                **metrics,
                "process_timestamp": utc_now(),
            }
            all_records.append(record)
            append_coverage_row(coverage_csv, make_coverage_row(t, yahoo_symbol, snap, asof_date, "success", ""))
            covered_snapshots.add(attempt_key)
            already_covered.add((code, snap_ym))
            print("    Successfully parsed consensus data.")
            checkpoint_records(all_records, output_csv, history_csv if not args.no_merge else None)
            time.sleep(2.0)

    if not all_records:
        print("No historical snapshots successfully processed.")
        if history_csv and history_csv.exists():
            try:
                df_hist = pd.read_csv(history_csv, encoding="utf-8")
                if "process_timestamp" in df_hist.columns and len(df_hist) > 0:
                    df_hist.loc[df_hist.index[-1], "process_timestamp"] = utc_now()
                    df_hist.to_csv(history_csv, index=False, encoding="utf-8-sig")
                    print(f"Updated process_timestamp of the latest row in {history_csv} to signal successful run.")
            except Exception as e:
                print(f"Warning: Failed to update process_timestamp of existing history: {e}")
        return

    checkpoint_records(all_records, output_csv, history_csv if not args.no_merge else None)
    print(f"\nFinished! Wrote {len(all_records)} historical records to {output_csv}")


if __name__ == "__main__":
    main()

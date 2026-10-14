#!/usr/bin/env python3
"""Generate the Yahoo Finance Wayback monthly coverage matrix.

Output: data/reports/raw_wayback_coverage_matrix.csv
  - Columns: stock_code, company_name, YYYY-MM (one col per month, 36-month window)
  - Values:  OK | MISSING
  - This is a monthly coverage summary for freshness reporting. Yahoo.Finance keeps
    per-snapshot fetch outcomes in raw_wayback_fetch_attempts.csv.

Usage:
  python scripts/gen_wayback_coverage_matrix.py
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]

HISTORY_CSV    = REPO_ROOT / "data" / "reports" / "raw_yahoo_finance_consensus_history.csv"
STOCK_LIST_CSV = REPO_ROOT / "StockID_TWSE_TPEX.csv"         # populated by scripts/Get觀察名單.py
FOCUS_LIST_CSV = REPO_ROOT / "StockID_TWSE_TPEX_focus.csv"   # populated by scripts/Get觀察名單.py
OUTPUT_MATRIX  = REPO_ROOT / "data" / "reports" / "raw_wayback_coverage_matrix.csv"

# Consensus columns that must have a value to count as "OK"
CONSENSUS_COLS = ["earnings_0y_avg", "revenue_0y_avg", "earnings_0q_avg", "revenue_0q_avg"]

# How many months back to include in the matrix (from today)
LOOKBACK_MONTHS = 36


def gen_month_range(lookback_months: int) -> list[str]:
    """Generate sorted list of YYYY-MM strings for the lookback window."""
    today = datetime.today()
    months = set()
    for i in range(lookback_months + 1):
        d = today.replace(day=1) - timedelta(days=i * 28)
        months.add(d.strftime("%Y-%m"))
    return sorted(months)


def load_stock_names() -> dict[str, str]:
    """Load stock code -> name mapping from StockID_TWSE_TPEX.csv."""
    name_map: dict[str, str] = {}
    if STOCK_LIST_CSV.exists():
        with open(STOCK_LIST_CSV, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get("代號") or "").strip()
                name = (row.get("名稱") or "").strip()
                if code:
                    name_map[code] = name
    return name_map


def load_focus_codes() -> set[str]:
    """Return the set of stock codes in the focus list."""
    codes: set[str] = set()
    if FOCUS_LIST_CSV.exists():
        with open(FOCUS_LIST_CSV, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get("代號") or "").strip()
                if code:
                    codes.add(code)
    return codes


def main() -> None:
    if not HISTORY_CSV.exists():
        print(f"[ERROR] History CSV not found: {HISTORY_CSV}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading history from: {HISTORY_CSV}")
    df = pd.read_csv(HISTORY_CSV, encoding="utf-8")
    df["stock_code"] = df["stock_code"].astype(str).str.strip()
    df["forecast_asof_date"] = pd.to_datetime(df["forecast_asof_date"], errors="coerce")
    df["year_month"] = df["forecast_asof_date"].dt.strftime("%Y-%m")

    # Only TW stocks (4-digit numeric codes)
    df_tw = df[df["stock_code"].str.match(r"^\d{4}$")].copy()

    all_stocks = sorted(df_tw["stock_code"].unique())
    month_cols = gen_month_range(LOOKBACK_MONTHS)

    # Build covered set: (stock_code, YYYY-MM) with at least one non-NaN consensus value
    covered: set[tuple[str, str]] = set()
    for _, row in df_tw.iterrows():
        ym = row.get("year_month")
        if not isinstance(ym, str) or len(ym) != 7:
            continue
        has_value = any(
            col in df_tw.columns
            and pd.notna(row.get(col))
            and str(row.get(col)).strip() not in ["", "nan", "NaN"]
            for col in CONSENSUS_COLS
        )
        if has_value:
            covered.add((row["stock_code"], ym))

    name_map    = load_stock_names()
    focus_codes = load_focus_codes()
    print(f"Focus stocks: {len(focus_codes)} | Full list: {len(all_stocks)}")

    # Build matrix rows
    rows: list[dict] = []
    ok_total = missing_total = 0
    now_utc_str = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    for stock in all_stocks:
        is_focus = stock in focus_codes
        row: dict = {
            "stock_code":        stock,
            "company_name":      name_map.get(stock, ""),
            "is_focus":          "Y" if is_focus else "N",
            "process_timestamp": now_utc_str,
        }
        for ym in month_cols:
            if (stock, ym) in covered:
                row[ym] = "OK"
                ok_total += 1
            else:
                row[ym] = "MISSING"
                missing_total += 1
        rows.append(row)

    # Sort: focus stocks first, then by stock_code
    rows.sort(key=lambda r: (0 if r["is_focus"] == "Y" else 1, r["stock_code"]))

    # Write matrix CSV
    OUTPUT_MATRIX.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["stock_code", "company_name", "is_focus", "process_timestamp"] + month_cols
    with open(OUTPUT_MATRIX, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    total    = ok_total + missing_total
    pct      = ok_total / total * 100 if total else 0
    focus_ok = sum(1 for r in rows if r["is_focus"] == "Y"
                   for ym in month_cols if r.get(ym) == "OK")
    print(f"\n=== Coverage Matrix Generated ===")
    print(f"Output  : {OUTPUT_MATRIX}")
    print(f"Stocks  : {len(all_stocks)} ({len(focus_codes)} focus + {len(all_stocks)-len(focus_codes)} standard)")
    print(f"Months  : {len(month_cols)} ({month_cols[0]} ~ {month_cols[-1]})")
    print(f"OK      : {ok_total} ({pct:.1f}%)")
    print(f"MISSING : {missing_total} ({100 - pct:.1f}%)")
    print(f"Focus OK: {focus_ok} / {len(focus_codes) * len(month_cols)} cells")
    print("\nThis matrix is synced to biztrends.TW for its freshness report.")
    print(f"Focus stocks (is_focus=Y) get deeper Wayback lookback (28m daily / 48m backfill).")


if __name__ == "__main__":
    main()

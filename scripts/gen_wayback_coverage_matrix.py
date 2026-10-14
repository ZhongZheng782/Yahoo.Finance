#!/usr/bin/env python3
"""Generate the monthly Yahoo / Wayback snapshot coverage matrix.

Output: data/reports/raw_wayback_coverage_matrix.csv
  - Columns: stock_code, company_name, YYYY-MM (one col per month, 36-month window)
  - Values: OK (Wayback) | OK(Y) (Yahoo daily snapshot) | MISSING
  - Wayback coverage is sourced only from successful entries in the attempt ledger;
    Yahoo coverage is sourced from dated daily consensus snapshots.

Usage:
  python scripts/gen_wayback_coverage_matrix.py
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

YAHOO_DAILY_CSV = REPO_ROOT / "data" / "reports" / "raw_yahoo_finance_consensus_daily.csv"
WAYBACK_ATTEMPTS_CSV = REPO_ROOT / "data" / "reports" / "raw_wayback_fetch_attempts.csv"
STOCK_LIST_CSV = REPO_ROOT / "StockID_TWSE_TPEX.csv"         # populated by scripts/Get觀察名單.py
FOCUS_LIST_CSV = REPO_ROOT / "StockID_TWSE_TPEX_focus.csv"   # populated by scripts/Get觀察名單.py
OUTPUT_MATRIX  = REPO_ROOT / "data" / "reports" / "raw_wayback_coverage_matrix.csv"

# Consensus columns that must have a value to count as "OK"
CONSENSUS_COLS = ["earnings_0y_avg", "revenue_0y_avg", "earnings_0q_avg", "revenue_0q_avg"]

# How many months back to include in the matrix (from today)
LOOKBACK_MONTHS = 36
WAYBACK_LATEST_MONTH = "2026-04"


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


def has_consensus_value(row: dict[str, str]) -> bool:
    return any(
        (row.get(col) or "").strip().lower() not in {"", "nan", "none", "na", "n/a"}
        for col in CONSENSUS_COLS
    )


def load_monthly_pairs(path: Path, stock_codes: set[str], *, require_success: bool = False) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    if not path.exists():
        print(f"[WARN] Coverage source not found: {path}", file=sys.stderr)
        return pairs

    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("stock_code") or "").strip()
            forecast_date = (row.get("forecast_asof_date") or "").strip()
            month = forecast_date[:7]
            if code not in stock_codes or len(month) != 7 or month[4] != "-":
                continue
            if require_success:
                if (row.get("status") or "").strip().lower() == "success":
                    pairs.add((code, month))
            elif has_consensus_value(row):
                pairs.add((code, month))
    return pairs


def main() -> None:
    if not STOCK_LIST_CSV.exists():
        print(f"[ERROR] Current Taiwan stock list not found: {STOCK_LIST_CSV}", file=sys.stderr)
        sys.exit(1)

    name_map = load_stock_names()
    if not name_map:
        print(f"[ERROR] No stock codes found in: {STOCK_LIST_CSV}", file=sys.stderr)
        sys.exit(1)

    # The current stock list is authoritative: include newly listed companies,
    # and drop codes no longer present in the list even if history remains.
    all_stocks = sorted(name_map)
    stock_codes = set(all_stocks)
    month_cols = gen_month_range(LOOKBACK_MONTHS)
    yahoo_covered = load_monthly_pairs(YAHOO_DAILY_CSV, stock_codes)
    wayback_covered = {
        (code, month)
        for code, month in load_monthly_pairs(WAYBACK_ATTEMPTS_CSV, stock_codes, require_success=True)
        if month <= WAYBACK_LATEST_MONTH
    }

    focus_codes = load_focus_codes()
    print(f"Focus stocks: {len(focus_codes)} | Full list: {len(all_stocks)}")

    # Build matrix rows
    rows: list[dict] = []
    ok_wayback_total = ok_yahoo_total = missing_total = 0
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
            if (stock, ym) in wayback_covered:
                row[ym] = "OK"
                ok_wayback_total += 1
            elif (stock, ym) in yahoo_covered:
                row[ym] = "OK(Y)"
                ok_yahoo_total += 1
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

    total    = ok_wayback_total + ok_yahoo_total + missing_total
    pct      = ok_wayback_total / total * 100 if total else 0
    focus_ok = sum(1 for r in rows if r["is_focus"] == "Y"
                   for ym in month_cols if r.get(ym) == "OK")
    print(f"\n=== Coverage Matrix Generated ===")
    print(f"Output  : {OUTPUT_MATRIX}")
    print(f"Stocks  : {len(all_stocks)} ({len(focus_codes)} focus + {len(all_stocks)-len(focus_codes)} standard)")
    print(f"Months  : {len(month_cols)} ({month_cols[0]} ~ {month_cols[-1]})")
    print(f"Wayback : only through {WAYBACK_LATEST_MONTH}")
    print(f"OK      : {ok_wayback_total} Wayback cells ({pct:.1f}%)")
    print(f"OK(Y)   : {ok_yahoo_total} Yahoo cells")
    print(f"MISSING : {missing_total} cells")
    print(f"Focus Wayback OK: {focus_ok} / {len(focus_codes) * len(month_cols)} cells")
    print("\nThis matrix is synced to biztrends.TW for its freshness report.")
    print(f"Focus stocks (is_focus=Y) get deeper Wayback lookback (28m daily / 48m backfill).")


if __name__ == "__main__":
    main()

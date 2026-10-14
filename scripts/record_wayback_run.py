#!/usr/bin/env python3
"""Record scheduled Wayback workflow runs and whether they produced data."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATTEMPTS_CSV = ROOT / "data/reports/raw_wayback_fetch_attempts.csv"
RUNS_CSV = ROOT / "data/reports/raw_wayback_run_history.csv"
RUN_COLUMNS = [
    "run_id",
    "run_attempt",
    "created_at",
    "event",
    "run_status",
    "fetch_step_outcome",
    "successful_items",
    "failed_items",
    "process_timestamp",
]
VALID_STATUSES = {"success", "download_failed", "parse_failed", "empty_metrics"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def count_attempts_since(start_line: int) -> tuple[int, int]:
    """Count status rows appended by this run after the pre-fetch line count."""
    if not ATTEMPTS_CSV.exists():
        return 0, 0
    with ATTEMPTS_CSV.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    recent = rows[max(0, start_line) :]
    statuses = [(row.get("status") or "").strip().lower() for row in recent]
    statuses = [status for status in statuses if status in VALID_STATUSES]
    return statuses.count("success"), sum(status != "success" for status in statuses)


def count_attempts_in_window(started_at: str, ended_at: str) -> tuple[int, int]:
    """Reconstruct an older run's output from timestamped snapshot records."""
    if not ATTEMPTS_CSV.exists():
        return 0, 0
    start = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    success = failures = 0
    for row in read_csv(ATTEMPTS_CSV):
        status = (row.get("status") or "").strip().lower()
        if status not in VALID_STATUSES:
            continue
        raw_time = (row.get("process_timestamp") or "").strip()
        if not raw_time:
            continue
        try:
            attempted_at = dt.datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        if attempted_at.tzinfo is None:
            # Legacy attempt logs used naive strings from a UTC clock.
            attempted_at = attempted_at.replace(tzinfo=dt.timezone.utc)
        if start <= attempted_at <= end:
            if status == "success":
                success += 1
            else:
                failures += 1
    return success, failures


def fetch_scheduled_runs(repo: str, workflow: str, token: str, since: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {"event": "schedule", "created": f">={since}", "per_page": 100, "page": 1}
    )
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?{query}"
    runs: list[dict] = []
    while url:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "wayback-run-ledger",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            runs.extend(payload.get("workflow_runs", []))
            link = response.headers.get("Link", "")
        next_url = ""
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part[part.find("<") + 1 : part.find(">")]
                break
        url = next_url
    return runs


def write_rows(rows: dict[tuple[str, str], dict[str, str]]) -> None:
    RUNS_CSV.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows.values(), key=lambda row: (row["created_at"], row["run_id"], row["run_attempt"]))
    with RUNS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RUN_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(ordered)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-outcome", required=True)
    parser.add_argument("--attempt-start-line", required=True, type=int)
    args = parser.parse_args()

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    workflow = os.environ.get("GITHUB_WORKFLOW_FILE", "Actions-fetch.yaml")
    token = os.environ.get("GITHUB_TOKEN", "")
    current_run_id = os.environ.get("GITHUB_RUN_ID", "")
    current_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if not all((repo, token, current_run_id)):
        print("::error::Missing GitHub workflow metadata needed to record Wayback run history")
        return 1

    successful_items, failed_items = count_attempts_since(args.attempt_start_line)
    fetch_outcome = args.fetch_outcome
    if fetch_outcome != "success" or failed_items > 0:
        run_status = "failure"
    elif successful_items > 0:
        run_status = "success"
    else:
        # A scheduled execution with no newly produced consensus item is not a
        # successful refresh, even if the script exited cleanly.
        run_status = "no_output"

    known = {
        (row.get("run_id", ""), row.get("run_attempt", "1")): row
        for row in read_csv(RUNS_CSV)
    }
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    query_since = cutoff.date().isoformat()
    try:
        api_runs = fetch_scheduled_runs(repo, workflow, token, query_since)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"::error::Could not reconcile scheduled Wayback runs from GitHub API: {exc}")
        return 1
    # Keep only the rolling audit window. Runs before this instrumentation
    # were installed are reconstructed from the timestamped snapshot ledger.
    known = {
        key: row for key, row in known.items()
        if row.get("created_at") and dt.datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")) >= cutoff
    }
    api_runs = [
        run for run in api_runs
        if run.get("created_at")
        and dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")) >= cutoff
    ]
    for run in api_runs:
        run_id = str(run.get("id", ""))
        attempt = str(run.get("run_attempt", "1"))
        key = (run_id, attempt)
        existing = known.get(key)
        if run_id == current_run_id and attempt == current_attempt:
            successful = successful_items
            failed = failed_items
            status = "partial" if successful > 0 and (fetch_outcome != "success" or failed > 0) else run_status
            step_outcome = fetch_outcome
            process_time = utc_now()
        elif existing:
            successful = int(existing.get("successful_items") or 0)
            failed = int(existing.get("failed_items") or 0)
            status = existing.get("run_status", "failure")
            step_outcome = existing.get("fetch_step_outcome", "unknown")
            process_time = existing.get("process_timestamp", "")
        else:
            run_state = (run.get("status") or "").lower()
            if run_state == "in_progress":
                continue
            started_at = run.get("run_started_at") or run.get("created_at")
            ended_at = run.get("updated_at") or utc_now()
            successful, failed = count_attempts_in_window(started_at, ended_at)
            step_outcome = run.get("conclusion") or run_state or "unknown"
            if successful > 0 and failed == 0 and step_outcome == "success":
                status = "success"
            elif successful > 0:
                status = "partial"
            elif step_outcome == "success":
                status = "no_output"
            else:
                status = "failure"
            process_time = utc_now()
        known[key] = {
            "run_id": run_id,
            "run_attempt": attempt,
            "created_at": run.get("created_at", ""),
            "event": "schedule",
            "run_status": status,
            "fetch_step_outcome": step_outcome,
            "successful_items": str(successful),
            "failed_items": str(failed),
            "process_timestamp": process_time,
        }

    # The API's list can omit the currently executing run for a short period.
    if event == "schedule" and (current_run_id, current_attempt) not in known:
        known[(current_run_id, current_attempt)] = {
            "run_id": current_run_id,
            "run_attempt": current_attempt,
            "created_at": utc_now(),
            "event": event,
            "run_status": run_status,
            "fetch_step_outcome": fetch_outcome,
            "successful_items": str(successful_items),
            "failed_items": str(failed_items),
            "process_timestamp": utc_now(),
        }
    write_rows(known)
    print(
        f"Recorded Wayback run {current_run_id}/{current_attempt}: {run_status}; "
        f"new items={successful_items}, failed snapshots={failed_items}; "
        f"scheduled runs reconciled={len(api_runs)}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

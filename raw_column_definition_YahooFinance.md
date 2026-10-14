---
source: Yahoo.Finance private repository, file raw_column_definition.md
destination: downstream definitions/raw_column_definition_YahooFinance.md
---

# Raw CSV Column Definitions - Yahoo.Finance

## raw_yahoo_finance.csv (Yahoo Finance Analyst Analysis, Long Format)
**No:** TBD
**Source:** `data/stage1_raw/raw_yahoo_finance.csv`
**Data Source:** Yahoo Finance HK analysis page via yfinance
**Update Frequency:** Daily automated updates
**Extraction Strategy:** Fetch structured yfinance analyst analysis tables and normalize them into long format. `source_url` points to the human-readable Yahoo HK analysis page. US ticker universe is read from `data/conceptstocks_raw/raw_conceptstock_company_metadata.csv`, synced from ConceptStocks.

### Section Mapping

| section | source_method | Yahoo page |
|---|---|---|
| `收益預估` | `get_revenue_estimate` | `/analysis/` |
| `盈利預估` | `get_earnings_estimate` | `/analysis/` |
| `盈利記錄` | `get_earnings_history` | `/analysis/` |
| `每股盈利走勢` | `get_eps_trend` | `/analysis/` |
| `每股盈利修改` | `get_eps_revisions` | `/analysis/` |
| `預計增長` | `get_growth_estimates` | `/analysis/` |

### Columns

| Column | Type | Description | Source Field | Notes |
|---|---|---|---|---|
| `stock_code` | string | Original stock code or ticker | TW list `代號`; ConceptStocks `Ticker` | e.g., `2330`, `NVDA` |
| `company_name` | string | Company display name | TW list `名稱`; ConceptStocks `公司名稱` |  |
| `market` | string | Market code | System | `TW`, `TWO`, `US`, `UNKNOWN` |
| `yahoo_symbol` | string | Yahoo Finance ticker symbol | System | e.g., `2330.TW`, `8299.TWO`, `NVDA` |
| `symbol_resolution_status` | string | Symbol resolution result | System | `resolved`, `fallback_to_two`, `not_found`, `skipped_index`, `skipped_private_or_missing_ticker` |
| `concept_name` | string | ConceptStocks concept name | ConceptStocks `概念欄位` | Blank for Taiwan stocks |
| `source_url` | string | Yahoo HK analysis URL | System | `https://hk.finance.yahoo.com/quote/{yahoo_symbol}/analysis/` |
| `section` | string | Analyst analysis section name | System | Chinese label from section mapping |
| `source_method` | string | yfinance method name | System | See section mapping |
| `period` | string | Forecast period or earnings date | yfinance DataFrame index/column | e.g., `0q`, `+1q`, `0y`, `+1y`, `+5y`, date |
| `metric` | string | Raw metric name | yfinance DataFrame index/column | e.g., `avg`, `low`, `high`, `upLast7days` |
| `metric_zh` | string | Chinese metric label | System | e.g., `平均值`, `最低值`, `近7天上修` |
| `value` | string | Raw metric value | yfinance | Kept as string-compatible numeric text |
| `unit` | string | Value unit classification | System | `eps`, `revenue`, `pct`, `count`, blank |
| `currency` | string | Currency code where applicable | System | `TWD` for TW/TWO, `USD` for US |
| `fetch_status` | string | Fetch row status | System | `success`, `empty`, `error`, `symbol_not_found`, `skipped_index`, `skipped_private_or_missing_ticker` |
| `error_message` | string | Error or skip reason | System | Blank for success rows |
| `source_metadata_timestamp` | datetime | Upstream metadata timestamp | ConceptStocks `process_timestamp` | Used for US metadata provenance |
| `download_timestamp` | datetime | Yahoo/yfinance retrieval timestamp | System | UTC `YYYY-MM-DD HH:MM:SS` |
| `process_timestamp` | datetime | CSV generation timestamp | System | UTC `YYYY-MM-DD HH:MM:SS` |

---

## raw_yahoo_finance_summary_latest.csv (Yahoo Finance Forecast Summary)

**Source:** `data/forecast/raw_yahoo_finance_summary_latest.csv`
**Data Source:** `raw_yahoo_finance.csv` plus synced FactSet detailed report
**Update Frequency:** Daily automated updates
**Extraction Strategy:** Pivot selected Yahoo metrics into one row per symbol, then cross-check current/next-year EPS and revenue estimates against `raw_factset_detailed_report.csv`.

### Columns

| Column | Type | Description | Source Field | Notes |
|---|---|---|---|---|
| `stock_code` | string | Original stock code or ticker | raw Yahoo | Primary symbol |
| `company_name` | string | Company display name | raw Yahoo |  |
| `market` | string | Market code | raw Yahoo | `TW`, `TWO`, `US` |
| `yahoo_symbol` | string | Yahoo Finance ticker symbol | raw Yahoo |  |
| `symbol_resolution_status` | string | Symbol resolution result | raw Yahoo |  |
| `concept_name` | string | ConceptStocks concept name | raw Yahoo | Blank for Taiwan stocks |
| `source_url` | string | Yahoo HK analysis URL | raw Yahoo | Human review link |
| `forecast_asof_date` | date | Forecast snapshot date | System | UTC date |
| `next_investor_event_date` | date | Next investor event date | Future integration | Blank in first version |
| `days_to_investor_event` | integer | Days until next investor event | Future integration | Blank in first version |
| `earnings_0q_avg` | float | Current-quarter average EPS estimate | Yahoo `盈利預估` | period `0q`, metric `avg` |
| `earnings_1q_avg` | float | Next-quarter average EPS estimate | Yahoo `盈利預估` | period `+1q`, metric `avg` |
| `earnings_0y_avg` | float | Current-year average EPS estimate | Yahoo `盈利預估` | period `0y`, metric `avg` |
| `earnings_1y_avg` | float | Next-year average EPS estimate | Yahoo `盈利預估` | period `+1y`, metric `avg` |
| `revenue_0q_avg` | float | Current-quarter average revenue estimate | Yahoo `收益預估` | period `0q`, metric `avg` |
| `revenue_1q_avg` | float | Next-quarter average revenue estimate | Yahoo `收益預估` | period `+1q`, metric `avg` |
| `revenue_0y_avg` | float | Current-year average revenue estimate | Yahoo `收益預估` | period `0y`, metric `avg` |
| `revenue_1y_avg` | float | Next-year average revenue estimate | Yahoo `收益預估` | period `+1y`, metric `avg` |
| `growth_5y_stock` | float | Long-term growth estimate | Yahoo `預計增長` | Usually period `+5y` |
| `eps_revision_0q_up_7d` | float | Current-quarter EPS estimate upward revisions in last 7 days | Yahoo `每股盈利修改` |  |
| `eps_revision_0q_down_7d` | float | Current-quarter EPS estimate downward revisions in last 7 days | Yahoo `每股盈利修改` | Metric name case differs upstream (`downLast7Days`); matched case-insensitively |
| `eps_revision_0q_up_30d` | float | Current-quarter EPS estimate upward revisions in last 30 days | Yahoo `每股盈利修改` |  |
| `eps_revision_0q_down_30d` | float | Current-quarter EPS estimate downward revisions in last 30 days | Yahoo `每股盈利修改` |  |
| `eps_trend_0y_current` | float | Current-year EPS estimate, current value | Yahoo `每股盈利走勢` | period `0y`, metric `current` |
| `eps_trend_0y_90d_ago` | float | Current-year EPS estimate as of ~90 days ago | Yahoo `每股盈利走勢` | Compare with `eps_trend_0y_current` for revision momentum |
| `eps_trend_1y_current` | float | Next-year EPS estimate, current value | Yahoo `每股盈利走勢` | period `+1y`, metric `current` |
| `eps_trend_1y_90d_ago` | float | Next-year EPS estimate as of ~90 days ago | Yahoo `每股盈利走勢` |  |
| `last_earnings_date` | date/string | Latest earnings history period (max date, not file order) | Yahoo `盈利記錄` |  |
| `last_eps_estimate` | float | Latest historical EPS estimate | Yahoo `盈利記錄` |  |
| `last_eps_actual` | float | Latest historical EPS actual | Yahoo `盈利記錄` |  |
| `eps_beat_count_4q` | string | Quarters (of last ≤4) where actual EPS ≥ estimate, e.g. `3/4` | Yahoo `盈利記錄` |  |
| `eps_surprise_avg_4q_pct` | float | Average earnings surprise % over last ≤4 quarters | Yahoo `盈利記錄` | `surprisePercent` × 100 (0.02 → 2.0) |
| `factset_available` | boolean | Whether FactSet row is available | FactSet report |  |
| `factset_md_latest_date` | date | Latest FactSet markdown date | FactSet `MD最新日期` |  |
| `factset_quality_score` | float | FactSet quality score | FactSet `品質評分` |  |
| `factset_eps_current_year_avg` | float | FactSet current-year EPS average | FactSet `{YYYY}EPS平均值` | `YYYY` is execution year |
| `eps_current_year_diff` | float | Yahoo current-year EPS minus FactSet | Derived |  |
| `eps_current_year_diff_pct` | float | Current-year EPS percentage difference | Derived | `(Yahoo - FactSet) / FactSet` |
| `factset_eps_next_year_avg` | float | FactSet next-year EPS average | FactSet `{YYYY+1}EPS平均值` |  |
| `eps_next_year_diff` | float | Yahoo next-year EPS minus FactSet | Derived |  |
| `eps_next_year_diff_pct` | float | Next-year EPS percentage difference | Derived |  |
| `factset_revenue_current_year_avg` | float | FactSet current-year revenue average normalized to Yahoo comparison scale | FactSet `{YYYY}營收平均值` + scale normalization |  |
| `revenue_current_year_diff` | float | Yahoo current-year revenue minus FactSet | Derived |  |
| `revenue_current_year_diff_pct` | float | Current-year revenue percentage difference | Derived |  |
| `factset_revenue_next_year_avg` | float | FactSet next-year revenue average normalized to Yahoo comparison scale | FactSet `{YYYY+1}營收平均值` + scale normalization |  |
| `revenue_next_year_diff` | float | Yahoo next-year revenue minus FactSet | Derived |  |
| `revenue_next_year_diff_pct` | float | Next-year revenue percentage difference | Derived |  |
| `factset_revenue_scale_applied` | string | Revenue scale factors applied to FactSet values before Yahoo comparison | Derived | e.g., `revenue_current_year=1000; revenue_next_year=1000` |
| `cross_check_status` | string | Yahoo vs FactSet comparison status | Derived | `matched`, `missing_factset_source`, `missing_factset`, `large_diff` |
| `cross_check_notes` | string | Cross-check notes | Derived |  |
| `forecast_signal_status` | string | Forecast signal status | Derived | `fresh`, `missing`, `large_diff` |
| `forecast_confidence` | float | Forecast confidence score | Derived | 0-100 |
| `fetch_status` | string | Overall Yahoo fetch status | raw Yahoo |  |
| `error_message` | string | Error or skip reason | raw Yahoo |  |
| `download_timestamp` | datetime | Latest Yahoo/yfinance retrieval timestamp | raw Yahoo | UTC |
| `process_timestamp` | datetime | Summary generation timestamp | System | UTC |

---

## raw_wayback_yahoo_finance_consensus.csv (Yahoo Finance Historical Consensus, Wide Format)

**Source:** `data/reports/raw_wayback_yahoo_finance_consensus.csv`  
**Data Source:** Yahoo Finance `/analysis` pages captured by the Wayback Machine  
**Update Frequency:** Daily bounded backfill with checkpointing  
**Extraction Strategy:** Query Wayback CDX snapshots for Yahoo Finance analysis pages, parse archived revenue and EPS estimate tables, normalize one successful monthly snapshot per stock/date into wide consensus columns.

### Columns

| Column | Type | Description | Source Field | Notes |
|---|---|---|---|---|
| `stock_code` | string | Taiwan stock code | Stock list `代號` | e.g., `2330` |
| `company_name` | string | Company display name | Stock list `名稱` |  |
| `forecast_asof_date` | date | Archived Yahoo analysis snapshot date | Wayback timestamp | `YYYY-MM-DD` derived from snapshot timestamp |
| `earnings_0q_avg` | float | Current-quarter average EPS estimate | Archived Yahoo earnings estimate table | Nullable when unavailable |
| `earnings_1q_avg` | float | Next-quarter average EPS estimate | Archived Yahoo earnings estimate table | Nullable when unavailable |
| `earnings_0y_avg` | float | Current-year average EPS estimate | Archived Yahoo earnings estimate table | Nullable when unavailable |
| `earnings_1y_avg` | float | Next-year average EPS estimate | Archived Yahoo earnings estimate table | Nullable when unavailable |
| `revenue_0q_avg` | float | Current-quarter average revenue estimate | Archived Yahoo revenue estimate table | Numeric values normalize `K/M/B/T` suffixes |
| `revenue_1q_avg` | float | Next-quarter average revenue estimate | Archived Yahoo revenue estimate table | Numeric values normalize `K/M/B/T` suffixes |
| `revenue_0y_avg` | float | Current-year average revenue estimate | Archived Yahoo revenue estimate table | Numeric values normalize `K/M/B/T` suffixes |
| `revenue_1y_avg` | float | Next-year average revenue estimate | Archived Yahoo revenue estimate table | Numeric values normalize `K/M/B/T` suffixes |
| `process_timestamp` | datetime | Row ingestion timestamp for this historical consensus item | System | `YYYY-MM-DD HH:MM:SS` or CST-suffixed timestamp; used for freshness monitoring |

---

## raw_wayback_fetch_attempts.csv (Yahoo Finance Wayback Snapshot Attempt Ledger)

**Source:** `data/reports/raw_wayback_fetch_attempts.csv`  
**Data Source:** Wayback Machine CDX snapshots and archived Yahoo Finance download/parse attempts  
**Update Frequency:** Updated during each bounded Wayback run  
**Extraction Strategy:** Append one row per attempted archived snapshot, including successful parses and known non-parseable snapshots. Later runs use this ledger to skip already-recorded snapshots and retry failures after their cooldown.

### Status Values

| status | Meaning |
|---|---|
| `success` | Snapshot downloaded, parsed, and produced at least one consensus value |
| `download_failed` | Snapshot HTML could not be downloaded after retries |
| `parse_failed` | Snapshot downloaded but no usable consensus values were parsed; the HTML is attached to the GitHub Actions run artifact when within the evidence size/count limits |
| `empty_metrics` | Tables parsed but all target consensus values were empty or NaN; the HTML is attached to the GitHub Actions run artifact when within the evidence size/count limits |

**Page evidence:** Parse failures, empty results, and partially populated results save their HTML in the GitHub Actions run artifact when within the evidence size/count limits. The artifact is retained for 14 days; partially populated results may still have attempt status `success` if at least one metric was extracted.

### Columns

| Column | Type | Description | Source Field | Notes |
|---|---|---|---|---|
| `stock_code` | string | Taiwan stock code | Stock list `代號` | e.g., `2330` |
| `company_name` | string | Company display name | Stock list `名稱` |  |
| `yahoo_symbol` | string | Yahoo Finance ticker symbol used for the archive query | System | e.g., `2330.TW` |
| `snapshot_timestamp` | string | Wayback snapshot timestamp | CDX `timestamp` | `YYYYMMDDHHMMSS` |
| `forecast_asof_date` | date | Snapshot date derived from `snapshot_timestamp` | System | `YYYY-MM-DD` |
| `original_url` | string | Original Yahoo Finance analysis URL stored by Wayback | CDX `original` | May be finance/hk/sg Yahoo domain |
| `status` | string | Snapshot processing result | System | See status values above |
| `message` | string | Error or status details | System | Blank for `success` |
| `process_timestamp` | datetime | UTC timestamp when the attempt row was written | System | ISO 8601 with `Z` suffix |

---

## raw_wayback_run_history.csv (Yahoo Finance Wayback Scheduled Run History)

**Source:** `data/reports/raw_wayback_run_history.csv`  
**Update Frequency:** Reconciled on each Yahoo Finance workflow run; frequency reporting uses scheduled runs from the last 30 days.  
**Success Rule:** A scheduled run is successful only when it emits at least one new consensus item and has no failed snapshot attempts. Runs with no output, failed snapshots, or an unsuccessful fetch step count as unsuccessful.

| Column | Description |
|---|---|
| `run_id` / `run_attempt` | GitHub Actions workflow run and attempt identifiers |
| `created_at` | Workflow run creation time in UTC |
| `event` | GitHub Actions event; only `schedule` contributes to regular failure frequency |
| `run_status` | `success`, `partial`, `failure`, or `no_output` |
| `fetch_step_outcome` | Wayback fetch step conclusion |
| `successful_items` / `failed_items` | New snapshot records produced by the run |
| `process_timestamp` | UTC time this run audit row was recorded |

---

## raw_yahoo_finance_daily_price.csv (Yahoo Finance Daily OHLC Price History, Long Format)

**Source:** `data/reports/raw_yahoo_finance_daily_price.csv`
**Data Source:** yfinance `download()` OHLC history, `period="10y"` (bootstrap), `auto_adjust=True`
**Update Frequency:** Daily automated updates (same schedule as `raw_yahoo_finance.csv`)
**Extraction Strategy:** For each TW watchlist code (`StockID_TWSE_TPEX.csv`), try `.TW` then `.TWO` and keep whichever suffix returns data (avoids maintaining a separate listed/OTC lookup table). For each US ticker in `data/conceptstocks_raw/raw_conceptstock_company_metadata.csv` (`Ticker` column, placeholder `-` rows skipped), fetch directly by ticker. A fixed `MACRO_SYMBOLS` list (`^DJI ^GSPC ^IXIC ^SOX TWD=X NQ=F` — US indices, USD/TWD rate, NASDAQ-100 futures; `market="MACRO"`) is fetched the same way for `GoogleSheet.Banks --pre-market` morning-briefing statistics (US session close vs next TW open-gap relationships are computed downstream from this raw file; the AI bellwethers NVDA/AVGO/AMD/MSFT/TSM already arrive via the ConceptStocks ticker list). One row per symbol per trading day, covering ~10 years so downstream long rolling windows (252-day 52-week high/low, MA240/MA360 ladders and their backtests) have enough history both to compute the indicator and to accumulate samples after it becomes computable — GoodInfo's `ShowDailyK_ChartFlow` source only retains ~1 year, which is far too short. **Incremental:** a symbol already present in the existing CSV only refetches from (last stored date − 5 days) through today (the 5-day overlap re-covers rows that `auto_adjust` may still be revising); a symbol with no prior rows bootstraps the full 10-year history. Merged output is capped to the most recent 3650 days (~10y, ≈50MB CSV — the safe ceiling under GitHub's 100MB single-file hard limit; going longer would require compression or per-market file splits). Pass `--full-refresh` to ignore the existing CSV and re-bootstrap every symbol.

### Columns

| Column | Type | Description | Source Field | Notes |
|---|---|---|---|---|
| `stock_code` | string | Original stock code or ticker | TW list `代號`; ConceptStocks `Ticker` | e.g., `2330`, `TSM` |
| `company_name` | string | Company display name | TW list `名稱`; ConceptStocks `公司名稱` |  |
| `market` | string | Market code | System | `TW`, `US`, or `MACRO` (TW includes both listed and OTC — see `yahoo_symbol` for the resolved suffix; MACRO = indices/FX/futures from `MACRO_SYMBOLS`) |
| `yahoo_symbol` | string | Resolved Yahoo Finance ticker symbol | System | e.g., `2330.TW`, `8299.TWO`, `TSM` |
| `交易_日期` | date | Trading date | yfinance index | `YYYY-MM-DD` |
| `開盤價` | float | Open price (split/dividend adjusted) | yfinance `Open` |  |
| `最高價` | float | High price (split/dividend adjusted) | yfinance `High` |  |
| `最低價` | float | Low price (split/dividend adjusted) | yfinance `Low` |  |
| `收盤價` | float | Close price (split/dividend adjusted) | yfinance `Close` | Primary field used for RSI/MA/52-week indicators downstream |
| `volume` | float | Trading volume | yfinance `Volume` |  |
| `download_timestamp` | datetime | Retrieval timestamp (shared across all rows fetched for a given symbol in one run) | System | UTC `YYYY-MM-DD HH:MM:SS` |

---

## raw_yahoo_finance_intraday_60m.csv (Yahoo Finance 60-Minute Intraday OHLC Price History, TW Only, Long Format)

**Source:** `data/reports/raw_yahoo_finance_intraday_60m.csv`
**Data Source:** yfinance `download()` OHLC history, `interval="60m"`, `period="730d"` (bootstrap), `auto_adjust=True`
**Update Frequency:** Daily automated updates (same schedule as `raw_yahoo_finance_daily_price.csv`)
**Extraction Strategy:** TW watchlist (`StockID_TWSE_TPEX.csv`) plus a fixed `EXTRA_SYMBOLS` list fetched with the raw symbol as-is (currently `NQ=F` — its 08:00 Taipei hourly bar is the historical proxy for "checking NASDAQ futures before the TW open" in `GoogleSheet.Banks --pre-market`); US concept-stock tickers are excluded since this feed otherwise serves `volume_profile()` in `GoogleSheet.Banks/fugle_stock_advisor.py`, which is TW-only (停泊股 park-stock ranking). Try `.TW` then `.TWO` per code, same suffix-resolution approach as the daily feed. One row per symbol per ~1-hour bar (roughly 5 bars/trading day, 9:00–13:30 TWSE session), replacing the prior approach of approximating intraday price/volume distribution from daily OHLC (which assumed each day's volume was spread uniformly across that day's high-low range) with real sub-daily bars — still not tick-level granularity, but the finest interval yfinance offers with a multi-year lookback for free. **Incremental:** a symbol already present in the existing CSV only refetches from (last stored bar's date − 3 days) through now; a symbol with no prior rows bootstraps the full available history (yfinance's `60m` interval returns roughly the last 2–3 years depending on symbol). Merged output is capped to the most recent 730 days. Pass `--full-refresh` to ignore the existing CSV and re-bootstrap every symbol.

### Columns

| Column | Type | Description | Source Field | Notes |
|---|---|---|---|---|
| `stock_code` | string | Original TW stock code | TW list `代號` | e.g., `2330` |
| `company_name` | string | Company display name | TW list `名稱` |  |
| `yahoo_symbol` | string | Resolved Yahoo Finance ticker symbol | System | e.g., `2330.TW`, `8299.TWO` |
| `時間戳` | datetime | Bar timestamp (bar start, exchange-local via yfinance) | yfinance index | `YYYY-MM-DD HH:MM:SS%z`, one row per ~60-minute bar |
| `開盤價` | float | Open price (split/dividend adjusted) | yfinance `Open` |  |
| `最高價` | float | High price (split/dividend adjusted) | yfinance `High` |  |
| `最低價` | float | Low price (split/dividend adjusted) | yfinance `Low` |  |
| `收盤價` | float | Close price (split/dividend adjusted) | yfinance `Close` |  |
| `volume` | float | Trading volume within the bar | yfinance `Volume` | Distributed across this bar's high-low range in `volume_profile()`, not the whole trading day |
| `download_timestamp` | datetime | Retrieval timestamp (shared across all rows fetched for a given symbol in one run) | System | UTC `YYYY-MM-DD HH:MM:SS` |


---

## raw_yahoo_finance_consensus_daily.csv (Daily Analyst Consensus History, Append-only)

**Source:** `data/reports/raw_yahoo_finance_consensus_daily.csv`
**Data Source:** Daily snapshots of key columns from `raw_yahoo_finance_summary_latest.csv` (which is otherwise overwritten each run); initial backfill reconstructed 2026-05-29 onward from this repo's git history of that file
**Update Frequency:** Daily append (dedup key `stock_code + forecast_asof_date`); rows are never rewritten
**Extraction Strategy / Purpose:** The forward-EPS signal family in `GoogleSheet.Banks` (forward-PE thresholds, EPS growth, revision momentum, earnings surprise) is the only part of the buy-candidate KPI without backtest calibration — analyst estimates lack a historical time series (the Wayback pipeline's parse success rate is too low). This file turns the daily pipeline itself into that time series: after ~6-12 months of accumulation, signal-day forward-PE / estimate-revision values can be regressed against subsequent 60/120-day returns exactly like the technical sub-scores were.

### Columns

Subset of `raw_yahoo_finance_summary_latest.csv` columns, kept verbatim: `stock_code`, `company_name`, `market`, `forecast_asof_date` (snapshot date, row key), `earnings_0q/1q/0y/1y_avg`, `revenue_0y/1y_avg`, `eps_trend_0y/1y_current`, `eps_beat_count_4q`, `eps_surprise_avg_4q_pct`, `cross_check_status`, `process_timestamp`.

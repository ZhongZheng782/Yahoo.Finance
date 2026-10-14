---
name: skill-yahoo-finance-fetch
description: 從 Yahoo Finance（含 Wayback Machine 歷史快照）抓取台股與美股的分析師預估、逐日/60分鐘價格歷史、以及歷史共識資料，輸出為長格式 raw CSV。
---

# Yahoo Finance Fetch Skill（Yahoo Finance 資料抓取技能）

此技能整合 `wenchiehlee/Yahoo.Finance` 專案中所有向 Yahoo Finance 抓取資料的腳本，涵蓋四種資料面向：

1. **分析師預估表**（`fetch_cli.py` + `yahoo_client.py`）：透過 `yfinance` 的 analysis 相關方法（收益/盈利預估、盈利記錄、EPS 走勢與修改、預計增長），輸出長格式 raw CSV。
2. **逐日收盤價歷史**（`fetch_daily_price.py`）：約 10 年台股 + 美股 + 總經/大盤脈絡（指數、匯率、期貨）逐日 OHLCV，增量抓取並自動合併去重。
3. **60 分鐘 K 線**（`fetch_intraday_60m.py`）：約 2 年台股 60 分鐘 K 線（yfinance 免費可得最細的 intraday 粒度），用於 Volume Profile 等分析。
4. **Wayback Machine 歷史共識**（`fetch_wayback_consensus.py`）：透過 Wayback Machine CDX API 找出 Yahoo Finance `/analysis` 頁面的歷史快照，解析並回填過去的分析師共識（EPS/營收預估），用於前瞻訊號回測。

## 適用場景

- 需要建立或更新台股與美股的分析師共識、價格歷史、Volume Profile 等下游分析所需的 raw CSV 資料源。
- 需要回填 Yahoo Finance 分析師共識的歷史軌跡（Yahoo 本身不提供歷史 API，只能靠 Wayback Machine 快照）。
- 部署到其他專案時，只要該專案根目錄有 `configs/default.yaml`（見下方「設定檔」），四支腳本即可直接沿用。

## 依賴需求

- `pandas`
- `pyyaml`
- `requests`
- `yfinance>=0.2.40`
- `python-dotenv`
- `numpy`

## 核心腳本與指令

腳本皆位於技能目錄下的 `scripts/`。所有腳本會從自身所在路徑向上尋找含有 `configs/default.yaml` 的目錄，並以該目錄作為 REPO_ROOT 來解析預設路徑（也可用環境變數 `YAHOO_FINANCE_REPO_ROOT` 強制指定），因此不論是放在登錄庫還是部署到消費端專案的任何深度都能正常運作。

### 1. 分析師預估表

```bash
python scripts/fetch_cli.py --market all --tw-list all --output-csv data/reports/raw_yahoo_finance.csv
```

- `--market`：`all` / `tw` / `us`。
- `--tw-list`：`all` / `focus`（對應 `configs/default.yaml` 中 `input.tw_all_list` / `input.tw_focus_list`）。
- `--specific-symbols`：逗號分隔股票代號/Ticker，覆蓋清單篩選。
- `--config`：YAML 設定檔路徑，預設 `configs/default.yaml`。
- `--output-csv`：輸出路徑，預設 `configs/default.yaml` 的 `output.raw_csv`。

### 2. 逐日收盤價歷史（增量抓取）

```bash
python scripts/fetch_daily_price.py --output-csv data/reports/raw_yahoo_finance_daily_price.csv
```

- `--tw-list` / `--us-list`：台股/美股清單 CSV 路徑，預設分別為 `StockID_TWSE_TPEX.csv` 與 `data/conceptstocks_raw/raw_conceptstock_company_metadata.csv`。
- `--full-refresh`：忽略既有 CSV，全部代號整段重抓（預設抓 10 年，`BOOTSTRAP_PERIOD`）。
- 增量邏輯：已有資料的代號只抓「既有最後一筆日期 - 5 天緩衝」到今天，同日以新抓的為準合併，只保留最近 `RETENTION_DAYS`（預設 3650 天）。
- 台股會依序嘗試 `.TW` 與 `.TWO` 後綴，抓得到的那個視為正確市場別。

### 3. 60 分鐘 K 線（僅台股）

```bash
python scripts/fetch_intraday_60m.py --output-csv data/reports/raw_yahoo_finance_intraday_60m.csv
```

- `--tw-list`：台股清單 CSV 路徑，預設 `StockID_TWSE_TPEX.csv`。
- `--full-refresh`：忽略既有 CSV，全部代號整段重抓（yfinance 60m interval 最長回溯 `BOOTSTRAP_PERIOD`，預設 730 天）。
- 增量邏輯與 `fetch_daily_price.py` 相同，但合併鍵是完整時間戳而非日期。

### 4. Wayback Machine 歷史共識回填

```bash
# 每日例行：12 個月回溯（focus 股票 28 個月、其餘 14 個月）
python scripts/fetch_wayback_consensus.py --max-runtime-minutes 300

# 一次性深度回填：focus 48 個月、其餘 24 個月
python scripts/fetch_wayback_consensus.py --backfill
```

- `--tw-list`：`all` / `focus`。
- `--specific-symbols`：逗號分隔股票代號，覆蓋清單篩選。
- `--limit-months`：覆蓋所有股票的回溯月數（優先於 `--backfill`）。
- `--max-runtime-minutes`：達到時間預算就優雅中止並提交部分結果（預設讀取 `configs/default.yaml` 的 `wayback.max_runtime_minutes`，即 300 分鐘），適合排進有時間限制的排程工作。
- `WAYBACK_CDP_URL`：必填的遠端 Chromium CDP endpoint；GitHub Actions 會連到共用 runner 上既有的瀏覽器，不會自行下載或啟動 Chromium。
- `--retry-failed-attempts`：也重抓先前已成功的快照；一般排程預設只略過成功快照，失敗快照會留待下次自動重試。
- `--no-merge`：只寫 `--output-csv`，不合併進 `--history-csv`。
- `--output-csv` / `--attempts-csv` (`--coverage-csv` 舊別名) / `--history-csv`：分別覆蓋批次輸出、快照嘗試紀錄、下游合併歷史 CSV 的路徑，預設對應 `configs/default.yaml` 的 `output.wayback_consensus_csv` / `output.wayback_fetch_attempts_csv` / `output.wayback_consensus_history_csv`。
- **本地化數值單位**：解析器支援 Yahoo Finance 頁面中的繁／簡體中文數值後綴，例如 `746.28十億`、`2.8萬億`，也支援英文 `B` / `M` / `T` / `K`。
- **不落地保存頁面**：Wayback HTML 只由 Chromium CDP 回應讀入記憶體並交給解析器；專用分頁停用 HTTP cache、拒絕瀏覽器下載並停用頁面腳本，workflow 不上傳 HTML artifact。磁碟只會保存設定所指定的 CSV 輸出與執行紀錄，不保存原始頁面。

#### Wayback 連線韌性（透過 runner 的 Chromium CDP）

archive.org 曾對 GitHub-hosted runner 回應 `Connection refused`、SSL handshake timeout、HTTP 429/503。Wayback 工作流程現在應在 `ZhongZheng782` self-hosted runner 上執行，並透過 `WAYBACK_CDP_URL` 連接既有 Chromium；CDX 連線探測、CDX 查詢與快照頁面下載都走同一個瀏覽器網路路徑。若仍使用 `urllib` 做 CDX 探測，失敗時會在啟動 Chromium 前退出，無法利用 CDP。

目前包含以下機制：

- **啟動前 CDP 連線與連線探測**：先透過 `WAYBACK_CDP_URL` 連上 runner 的 Chromium，再經由瀏覽器對 CDX API 探測。若 CDP 連線失敗或連續 3 次探測都發生網路層錯誤，會把錯誤寫入 `raw_wayback_cdx_queries.csv`、印出 `::error::` annotation，並以 exit code 1 結束。
- **執行中斷路器**：即使探測時連線正常，若執行途中連續 `NETWORK_BLOCK_STREAK_LIMIT`（預設 15）次請求都是網路層錯誤，會視為連線在執行途中被節流，提前中止並 checkpoint 已抓到的資料，同樣印出 `::error::` annotation。
- **分辨「網路層失敗」vs「合法空結果」**：CDX 查詢回傳格式正確但沒有快照（例如該股票這段期間本來就沒有存檔）不算失敗，不會觸發上述機制；連線錯誤、SSL 錯誤、timeout、HTTP 429/503 才算。
- **重試改用指數退避 + 隨機抖動 + 尊重 `Retry-After`**：CDX 查詢與快照下載的重試間隔從固定 3 秒改為 `3 * 2^attempt` 秒（上限 30 秒）加上隨機抖動；遇到 HTTP 503 且伺服器帶有 `Retry-After` header 時優先採用該值。
- **降低單支股票的請求量**：原本每支股票固定查詢 3 個 Yahoo 鏡像網域（finance.yahoo.com / hk.finance.yahoo.com / sg.finance.yahoo.com），現在只要累積到的月度快照數已達 `limit_months`，就會提前跳過剩餘網域，減少對 CDX API 的請求量。
- **保存 CDX 可稽核資料並使用同月備份**：`raw_wayback_cdx_queries.csv` 逐次保存查詢結果、重試錯誤、候選數，以及未查詢鏡像的略過原因；`raw_wayback_cdx_candidates.csv` 保存每個回傳時間戳及其選取／略過原因；每月最多依 `wayback.max_candidates_per_month` 選取 3 份候選，最新候選失敗時依序嘗試同月舊備份。`raw_wayback_fetch_attempts.csv` 保存下載重試次數、HTTP 狀態、解析結果及非空指標數。三者以 `run_id`、`query_id`、`candidate_id` 關聯，並同步到 biztrends.TW。

這些機制解決的是「讓失敗立即可見、減少無謂的請求量」；如果 archive.org 真的是針對 GitHub Actions 的 IP 段做硬性封鎖，唯一保證有效的做法是把這個步驟改到不同來源 IP 的環境執行（例如 self-hosted runner 或透過 proxy）。

## 設定檔（`configs/default.yaml`）

腳本共用同一份設定檔，需在消費端專案根目錄提供，範例：

```yaml
input:
  tw_all_list: StockID_TWSE_TPEX.csv
  tw_focus_list: StockID_TWSE_TPEX_focus.csv
  us_metadata: data/conceptstocks_raw/raw_conceptstock_company_metadata.csv

output:
  raw_csv: data/reports/raw_yahoo_finance.csv
  wayback_consensus_csv: data/reports/raw_wayback_yahoo_finance_consensus.csv
  wayback_fetch_attempts_csv: data/reports/raw_wayback_fetch_attempts.csv
  wayback_cdx_queries_csv: data/reports/raw_wayback_cdx_queries.csv
  wayback_cdx_candidates_csv: data/reports/raw_wayback_cdx_candidates.csv
  wayback_consensus_history_csv: data/reports/raw_yahoo_finance_consensus_history.csv

wayback:
  max_runtime_minutes: 300
  max_candidates_per_month: 3
  focus_priority_months: ["2026-01", "2025-06", "2025-01"]

yahoo:
  source_url_template: "https://hk.finance.yahoo.com/quote/{yahoo_symbol}/analysis/"
  taiwan_default_suffix: ".TW"
  taiwan_fallback_suffix: ".TWO"
  rate_limit_seconds: 1.0
```

`focus_priority_months` 會讓 focus 清單優先處理指定月份；這些月份先前失敗的快照會重試，已成功收錄的股票月份則會略過。

台股清單 CSV 需含「代號」「名稱」欄位；美股清單 CSV 需含「Ticker」「公司名稱」欄位（`fetch_cli.py` 另需「概念欄位」「process_timestamp」欄位）。

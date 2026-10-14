# Wayback CDX 診斷資料

Yahoo.Finance 每日 Wayback 工作流程會把 CDX 查詢、CDX 候選快照與實際抓取結果分別保存，三份資料以 `run_id`、`run_attempt`、`query_id`、`candidate_id` 關聯。

| 檔案 | 一列代表 | 用途 |
| --- | --- | --- |
| `raw_wayback_cdx_queries.csv` | 一次 CDX HTTP 請求；每次重試各一列。全域連線探測以 `stock_code=GLOBAL` 記錄；未查詢的鏡像網址也會以 `result=skipped` 記錄。 | 區分連線探測失敗、查詢成功、合法空結果、HTTP/網路錯誤，以及因月數上限而略過的網域。 |
| `raw_wayback_cdx_candidates.csv` | CDX 回傳的一個存檔時間戳。 | 保存候選 URL、月份、MIME type、原始存檔狀態碼、digest、長度，以及候選是否被選中與原因。 |
| `raw_wayback_fetch_attempts.csv` | 一次候選頁面的下載及解析結果。 | 保存下載重試數、最終 HTTP 狀態、錯誤、解析結果與非空指標數。失敗候選在後續排程仍可重試。 |

候選庫會保留查詢回傳的所有時間戳；實際選取仍遵守 `configs/default.yaml` 的 Wayback 截止月份與每檔股票回看月數。每月最多選取 `wayback.max_candidates_per_month` 份存檔狀態為 HTTP 200 且 MIME type 為 HTML 的快照（預設 3 份），依時間由新到舊嘗試；最新快照下載或解析失敗時會接著嘗試同月下一份候選。非 200 或非 HTML 候選會保留在清單中，但不會下載解析。

`candidate_count` 是該 URL 本次成功 CDX 回應回傳的候選數；被略過或失敗的 CDX 查詢不代表候選數為零。跨 Yahoo 鏡像網址的結果應以同一股票、年月及快照時間去重後再統計可選月份。

# Frontend Workflow

`frontend/` 是本機 web UI，用來批次上傳表格截圖，執行既有 OCR + Excel workflow，最後提供 `MIP_filled.xls` 與預覽圖。

前端不會把上傳檔寫進根目錄 `final-table/`。每次 job 都在 `frontend/runs/<job-id>/` 裡獨立保存 input、OCR output、Excel output 與 summary。

## 安裝依賴

在專案根目錄執行：

```powershell
uv pip install --python .\.venv-finetune\Scripts\python.exe -r .\frontend\requirements.txt
```

## 啟動

本機使用：

```powershell
.\.venv-finetune\Scripts\python.exe -m uvicorn frontend.app:app --host 127.0.0.1 --port 8000
```

開啟：

```text
http://127.0.0.1:8000
```

同一個區網其他電腦要連線時：

```powershell
.\.venv-finetune\Scripts\python.exe -m uvicorn frontend.app:app --host 0.0.0.0 --port 8000
```

其他電腦開：

```text
http://主機IP:8000
```

對外分享時不要加 `--reload`，避免檔案變更造成服務重啟。

## 密碼

密碼放在 `frontend/.env`。如果 `FRONTEND_ACCESS_PASSWORD` 留空，前端會是開放模式。

建立設定檔：

```powershell
Copy-Item .\frontend\.env.example .\frontend\.env
notepad .\frontend\.env
```

填入：

```text
FRONTEND_ACCESS_PASSWORD=請換成你的密碼
```

這只是簡單共享密碼，適合內網或 VPN。公開網路需要 HTTPS、反向代理與正式帳號權限控管。

## Job 資料夾

每次上傳會建立：

```text
frontend/runs/<job-id>/
```

內部結構：

```text
input/                 上傳的表格圖片
ocr-output/            ocr_final.py 產生的 .md/.json、GD image sidecar 與 PNG crops
excel-output/          run_workflow.py 產生的 workbook、debug、preview
job.json               前端輪詢用狀態
worker-summary.json    後端 worker 完整 summary
```

常看的檔案：

```text
frontend/runs/<job-id>/worker-summary.json
frontend/runs/<job-id>/ocr-output/<stem>.json
frontend/runs/<job-id>/ocr-output/<stem>_image_assets.json
frontend/runs/<job-id>/excel-output/<stem>/MIP_filled.xls
frontend/runs/<job-id>/excel-output/<stem>/MIP_filled_MIP_Results_snapshot.png
```

`frontend/runs/` 已被 `frontend/.gitignore` 排除。

## Worker 流程

1. 收集 `input/` 裡的圖片。
2. 暫時把 `ocr_final.INPUT_DIR` 指到該 job 的 `input/`，`ocr_final.OUTPUT_DIR` 指到該 job 的 `ocr-output/`。
3. 強制執行 `ocr_final.py`，產生 OCR JSON 和 GD image assets。
4. 排除 `*_image_assets.json`，把真正的 table JSON 交給 `src/run_workflow.py`。
5. 產生每張圖對應的 `MIP_filled.xls`、debug JSON 與 snapshot/preview。

目前 GD 列採 image passthrough：GD frame 會以原圖 crop 貼進 Excel；`SURFACE FINISH`、`SURFACE ROUGHNESS` 等非 GD 列不會被截圖，會走文字 OCR/parser。

## 預覽圖

workflow 會先嘗試使用 Excel COM snapshot。若本機 Excel COM 不可用，frontend worker 會用 `xlrd` + `Pillow` 產生簡化表格預覽 PNG，讓前端仍能檢視主要輸出。

snapshot 失敗但 workbook validation 成功時，通常只是預覽圖問題，不代表 `MIP_filled.xls` 寫入失敗。

## 前端狀態

瀏覽器頁面只保留目前頁面建立的 job。刷新或重新開頁面時，不會自動載入舊 job，但資料仍在 `frontend/runs/`。

如果頁面刷新或關閉時仍有 queued/running job，前端會送取消通知，後端會終止該 job 的 worker process，並把 `job.json` 標記為 `cancelled`。取消不會刪除 job 資料夾，也不會停止 uvicorn。

## 連線排錯

- 其他裝置開頁面時，伺服器 log 若看到 `GET /api/auth 200 OK`，代表網路與防火牆大致已通。
- 按登入後，log 應看到 `POST /api/login 200 OK`。如果只有 `GET /api/auth`，重新整理登入頁或清除快取。
- 若 log 出現 `Shutting down` / `Finished server process`，代表 uvicorn 已停止，需要重新啟動。
- 若 job 失敗，先看 `frontend/runs/<job-id>/worker-summary.json` 的 `errors`，再看 `ocr-output/` 是否產生 JSON。

## 手動重跑某個 job

可直接執行 worker，適合排查既有 run：

```powershell
.\.venv-finetune\Scripts\python.exe .\frontend\worker.py `
  --job-dir .\frontend\runs\<job-id> `
  --input-dir .\frontend\runs\<job-id>\input `
  --ocr-output-dir .\frontend\runs\<job-id>\ocr-output `
  --workflow-output-dir .\frontend\runs\<job-id>\excel-output
```

手動跑 worker 會更新 `worker-summary.json` 和 artifacts；若不是透過 web app 啟動，不一定會同步更新前端輪詢中的 `job.json`。

# Frontend Workflow

這個資料夾是一個獨立的本機操作介面，用來把多張表格截圖送進既有 OCR/Excel workflow，並在完成後提供 MIP Excel 下載與預覽圖。

## 安裝依賴

在專案根目錄執行：

```powershell
uv pip install --python .\.venv-finetune\Scripts\python.exe -r .\frontend\requirements.txt
```

## 啟動

在專案根目錄執行：

```powershell
.\.venv-finetune\Scripts\python.exe -m uvicorn frontend.app:app --host 127.0.0.1 --port 8000
```

開啟：

```text
http://127.0.0.1:8000
```

## 連接密碼

密碼放在 `frontend/.env`。這個檔案已被 `.gitignore` 排除，不會被提交。若 `FRONTEND_ACCESS_PASSWORD` 留空，前端會維持開放模式。

第一次設定可以先複製範本：

```powershell
Copy-Item .\frontend\.env.example .\frontend\.env
notepad .\frontend\.env
```

在 `.env` 填入：

```text
FRONTEND_ACCESS_PASSWORD=請換成你的密碼
```

本機啟動：

```powershell
.\.venv-finetune\Scripts\python.exe -m uvicorn frontend.app:app --host 127.0.0.1 --port 8000
```

如果要讓同一個區網的其他電腦連線，搭配 `0.0.0.0` 啟動：

```powershell
.\.venv-finetune\Scripts\python.exe -m uvicorn frontend.app:app --host 0.0.0.0 --port 8000
```

其他電腦使用：

```text
http://主機IP:8000
```

這是簡單共享密碼，適合內網或 VPN。若要放到公開網路，請加 HTTPS、反向代理和正式帳號權限控管。

### 連線排錯

- 其他裝置開頁面時，伺服器 log 若看到 `GET /api/auth 200 OK`，代表防火牆和網路已經通。
- 按下登入後，伺服器 log 應該要看到 `POST /api/login 200 OK`。若只有一直出現 `GET /api/auth`，請重新整理登入頁，或清除該頁快取後再試。
- 若 log 出現 `Shutting down` / `Finished server process`，代表 uvicorn 已停止。需要重新啟動服務。
- 對外分享時建議不要加 `--reload`，避免檔案變更造成服務重啟。

## 資料位置

每次上傳會建立一個隨機 job 資料夾：

```text
frontend/runs/<job-id>/
```

內部會保存：

- `input/`: 使用者上傳的表格截圖。
- `ocr-output/`: `ocr_final.py` 產生的 Markdown/JSON。
- `excel-output/`: `run_workflow.py` 產生的 extraction/debug、`MIP_filled.xls` 與預覽圖。
- `job.json`: 前端輪詢用的狀態、進度、警告、錯誤與 artifact 路徑。
- `worker-summary.json`: 後端 worker 的完整 workflow summary。

這個 frontend 不會把上傳檔案寫入根目錄 `final-table`，而是匯入既有 `ocr_final.py` 後，對單一 job 臨時指定 `INPUT_DIR` 與 `OUTPUT_DIR`。

## 前端工作狀態

前端畫面只保留目前瀏覽器頁面建立的 job。刷新瀏覽器或重新開頁面時，不會載入 `frontend/runs/` 裡先前完成的工作，但那些資料仍會留在伺服器硬碟。

如果頁面刷新或關閉時仍有 queued/running job，前端會送出取消通知，後端會終止該 job 的 worker process 並把 `job.json` 標記為 `cancelled`。這個取消動作不會刪除 job 資料夾，也不會停止 uvicorn 服務。

## 預覽圖

workflow 會先嘗試使用原本的 Excel COM snapshot。若本機 Excel COM 拒絕呼叫或不可用，frontend worker 會用 `xlrd` + `Pillow` 產生一張簡化的表格預覽 PNG，讓前端仍可先檢視輸出內容。

## 注意事項

- OCR 會載入 GLM-OCR 與 GD symbol classifier，後端目前用單一 lock 串行處理 job，避免多個 job 同時佔用 GPU。
- 若服務重啟，尚未完成的 job 會被標記為 failed；原始上傳檔與已產生 artifact 仍保留在該 job 資料夾。
- `frontend/runs/` 已由 `frontend/.gitignore` 排除。

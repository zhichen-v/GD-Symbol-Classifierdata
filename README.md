# GLM-OCR GD&T Table OCR

這個專案把 GD&T 表格截圖轉成最終 MIP Excel。現行穩定版不再嘗試解析 GD 符號本身，而是：

1. `ocr_final.py` 用 `zai-org/GLM-OCR` 做 cell-by-cell OCR。
2. 對 `GD`、`GD&T`、`FLATNESS`、`TRUE POSITION` 等 GD characteristic 列，直接擷取完整 feature-control-frame 圖片，輸出 sidecar image assets。
3. `src/run_workflow.py` 把 OCR JSON 轉成 extraction schema，尺寸/粗糙度等文字列走 parser，GD 圖片列跳過 parser。
4. `src/fill_template.py` 把 GD frame 圖片貼進 `template.xls` 的 Specification 欄，產生 `MIP_filled.xls`。

如果只想從現有 OCR JSON 產 Excel，不需要重新跑 OCR。

## 主要檔案

- `ocr_final.py`：正式 full-table OCR 入口，輸出 `.md`、`.json`、`*_image_assets.json` 與 `*_assets/*.png`。
- `src/run_workflow.py`：讀取 OCR JSON，產生 final extraction/debug/workbook。
- `src/run_ocr_workflow.py`：先跑 `ocr_final.py`，再跑 Excel workflow。
- `src/glm_table_adapter.py`：把 GLM-OCR table array JSON 轉成 workflow extraction schema，並讀取 GD image sidecar。
- `src/run_pipeline.py`：處理 parser、tolerance、equipment，GD image rows 直接 passthrough。
- `src/fill_template.py`：寫入 `template.xls`，並把 GD frame 圖片貼到 Specification 欄。
- `src/validate_output.py`：驗證 workbook 與 debug output。
- `frontend/`：本機 web UI，可批次上傳多張表格圖片並下載 Excel。
- `final-table/input/`：正式要辨識的表格圖片。
- `final-table/output/`：OCR 與 Excel workflow artifacts。

## 環境安裝

在專案根目錄建立 `.venv-finetune`：

```powershell
py -m venv .venv-finetune
.\.venv-finetune\Scripts\python.exe -m ensurepip --upgrade
.\.venv-finetune\Scripts\python.exe -m pip install --upgrade pip
.\.venv-finetune\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 包含 GLM-OCR 與 `.xls` 後處理所需套件。若是新 GPU 環境，建議先依 PyTorch 官方指令安裝符合 CUDA 版本的 `torch` / `torchvision`，再安裝 `requirements.txt`。

## Hugging Face Token

第一次使用或需要下載 base model 時，複製 `.env`：

```powershell
Copy-Item .env.example .env
```

填入：

```dotenv
HF_TOKEN=hf_your_token_here
HF_XET_HIGH_PERFORMANCE=1
```

`.env` 已被 `.gitignore` 排除，不要提交實際 token。

## OCR 加 Excel 一次跑

把表格圖片放到 `final-table/input/`，然後執行：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_ocr_workflow.py --force
```

第一次需要允許下載 GLM-OCR base model 時：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_ocr_workflow.py --force --download-base-model
```

若要跳過 Excel snapshot，適合自動檢查：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_ocr_workflow.py --force --no-snapshot
```

輸出範例：

```text
final-table/output/test.json
final-table/output/test_image_assets.json
final-table/output/test_assets/row005_gd_frame.png
final-table/output/test/MIP_filled.xls
final-table/output/test/test_extraction.json
final-table/output/test/extraction_debug.json
final-table/output/test/MIP_filled_MIP_Results_snapshot.png
```

## 只用現有 JSON 產 Excel

不重新跑 OCR，只處理 `final-table/output/*.json`：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_workflow.py --no-snapshot
```

指定單一 JSON：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_workflow.py `
  --input .\final-table\output\test.json `
  --output-root .\final-table\output `
  --no-snapshot
```

`run_workflow.py` 會自動排除 `*_image_assets.json`、`*_extraction.json`、`extraction_debug.json`。

## GD Image Passthrough

GD characteristic rows 會直接擷取完整 GD frame 圖片並貼到 Excel，不再把符號裁成單一 crop，也不再由 classifier 判斷 `[GD_POSITION]` 之類標籤。

這個策略的目的：

- 避免 GD symbol classifier/parser 誤判。
- 保留原圖的 GD 符號、數值、datum 與 modifiers。
- 讓 Excel Specification 欄呈現與原表格一致的視覺結果。

非 GD characteristic 不會進入截圖流程。例如 `SURFACE FINISH`、`SURFACE ROUGHNESS`、`DIMENSION` 仍維持 OCR 文字與後處理 parser。

要調整貼到 Excel 的 GD 圖片大小，改：

```python
# src/fill_template.py
SPECIFICATION_IMAGE_CELL_SCALE = 0.7
```

數字越小圖片越小；位置會自動置中。

## 文字後處理規則

非 GD image rows 仍會做文字解析與正規化：

- `Ø`、`⌀`、`∅`、`ø` 正規化為 `Ø`。
- `\pm`、`/pm`、`$ \pm0.02 $` 轉成 `±0.02`。
- `\mu`、`/mu` 轉成 `µ`。
- LaTeX 上下標公差會攤平成 parser 可處理格式。
- `17.9 0/-0.04`、`0.50/-0.04` 會拆成 specification 與 tolerance。
- 單邊公差補齊正負兩側：`+0.2` -> `+0.2/-0`，`-0.2` -> `+0/-0.2`。
- `±0.2` 本身代表正負兩側，維持 `±0.2`。

直徑不是 GD characteristic；不要輸出 `[DIAMETER]`，要保留字面 `Ø`。

## 驗證

修改 `src/` 後處理、OCR JSON、`template.xls`、`example.xls` 或 tolerance profile 後，至少跑：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_workflow.py --no-snapshot
```

成功時 summary 應該包含：

```json
{
  "status": "success"
}
```

每個 processed table 的 `workbook_validation.status` 也應為 `success`，並產生 `MIP_filled.xls`。

如果需要檢查預覽圖，不加 `--no-snapshot`。snapshot 依賴本機 Excel COM；若 snapshot 失敗但 workbook validation 成功，通常是 preview 問題，不代表 Excel 檔壞掉。

## 前端介面

啟動本機 web UI：

```powershell
.\.venv-finetune\Scripts\python.exe -m uvicorn frontend.app:app --host 127.0.0.1 --port 8000
```

開啟：

```text
http://127.0.0.1:8000
```

更多設定見 `frontend/README.md`。

## Classifier 與 Fine-tuning

目前正式 Excel 輸出不依賴 LoRA 或 GD symbol classifier。舊 LoRA 對 full-table GD&T 不可靠，因為 full-table 符號太小；symbol classifier 相關資料與腳本仍保留作研究、備援或後續實驗。

沒有明確需求時，不要重訓模型，也不要把 classifier 結果當成 GD label 語意正確的證明。

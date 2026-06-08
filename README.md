# GLM-OCR GD&T Table OCR

此專案的目標是把 GD&T 表格圖片轉成最終 MIP Excel。現行流程分成兩段：

1. `ocr_final.py` 讀取 `final-table/input/` 的表格圖片，使用 `zai-org/GLM-OCR` 與 GD&T symbol classifier 產生 Markdown/JSON。
2. `src/run_workflow.py` 讀取已處理好的 `final-table/output/*.json`，沿用舊專案 `GDT-Extraction-FB` 的後處理邏輯，補公差、判斷量測設備、寫入 `template.xls`，產生最終 `MIP_filled.xls`。

如果只想從現有 JSON 產 Excel，不需要重新跑 OCR。

## 目前架構

- `ocr_final.py`：正式表格 OCR 入口。只把 cell crop 放在記憶體中處理，輸出 `.md` 與 `.json`。
- `src/run_workflow.py`：以現有 `final-table/output/*.json` 產生最終 Excel。
- `src/run_ocr_workflow.py`：先執行 `ocr_final.py`，再執行 JSON 到 Excel 的 workflow。
- `src/glm_table_adapter.py`：把 GLM-OCR 的表格陣列 JSON 轉成舊 pipeline 使用的 extraction schema。
- `src/parse_specification.py`：規格文字、GD&T tag、LaTeX 公差、單位、公差欄位解析。
- `src/fill_template.py`：沿用 `template.xls` 樣式填入 MIP 結果。
- `src/validate_output.py`：檢查輸出的 workbook 是否符合 template 與 debug 資料。
- `final-table/input/`：正式要辨識的表格圖片。
- `final-table/output/`：OCR 產生的 `.md`、`.json`，以及 workflow 產生的子資料夾。
- `symbol-classifierdata/`：GD&T characteristic symbol classifier 的資料、split、訓練與評估腳本。

重要規則：

- GD&T characteristic 在 OCR JSON 中使用 `[GD_POSITION]`、`[GD_FLATNESS]` 這類 tag。
- 最大實體狀態使用 `[M]`。
- 直徑不是 GD characteristic；直徑要保留為字面符號 `Ø`，不要輸出 `[DIAMETER]`。
- 自動檢查只能確認格式可處理，不能證明 OCR 標籤語意一定正確。

## 安裝環境

建議在專案根目錄建立並使用 `.venv-finetune`：

```powershell
py -m venv .venv-finetune
.\.venv-finetune\Scripts\python.exe -m ensurepip --upgrade
.\.venv-finetune\Scripts\python.exe -m pip install --upgrade pip
.\.venv-finetune\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 包含 GLM-OCR 所需套件，以及 Excel `.xls` 套版輸出所需的 `xlrd`、`xlwt`、`xlutils`、`pywin32`。

若新裝置有 NVIDIA GPU，建議先依 PyTorch 官方指令安裝符合 CUDA 版本的 `torch` / `torchvision`，再安裝 `requirements.txt`。否則可能裝到 CPU 版，速度會很慢。

## Hugging Face Token

複製範本：

```powershell
Copy-Item .env.example .env
```

然後在 `.env` 填入自己的 token：

```dotenv
HF_TOKEN=hf_your_token_here
HF_XET_HIGH_PERFORMANCE=1
```

`.env` 會被 `.gitignore` 排除，不要提交實際 token。

## 只用現有 JSON 產 Excel

這是目前最常用、也最安全的後處理入口。它不會重新跑 OCR，只處理 `final-table/output/*.json`：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_workflow.py --no-snapshot
```

預設輸入：

```text
final-table/output/*.json
```

每個 JSON 會產生一個同名子資料夾，例如：

```text
final-table/output/test/MIP_filled.xls
final-table/output/test/test_extraction.json
final-table/output/test/extraction_debug.json
```

`--no-snapshot` 會跳過 Excel COM 截圖，適合自動驗證。若不加 `--no-snapshot`，workflow 會嘗試產生 workbook snapshot PNG；截圖失敗會列為 warning，不代表 workbook 寫入失敗。

常用參數：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_workflow.py `
  --input .\final-table\output\test.json `
  --output-root .\final-table\output `
  --no-snapshot
```

## OCR 加 Excel 一次跑

如果要先更新 OCR JSON，再產 Excel：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_ocr_workflow.py --no-snapshot
```

如果要強制重跑 OCR：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_ocr_workflow.py --force --no-snapshot
```

第一次需要允許下載 GLM-OCR base model 時：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_ocr_workflow.py --force --download-base-model --no-snapshot
```

`run_ocr_workflow.py` 會呼叫 `ocr_final.py`，所以仍需要 `symbol-classifierdata/output/best.pt` 存在，除非另外傳給 `ocr_final.py` 的相關參數。

## GLM JSON 後處理規則

`src/run_workflow.py` 會先把 GLM-OCR 的表格陣列 JSON 轉成舊專案 pipeline 的 extraction schema。轉換時依 header 名稱找欄位，所以 JSON 有沒有 `BUBBLE` 欄都可以。

後處理會處理這些 GLM 常見格式：

- `[GD_POSITION]`、`[GD_FLATNESS]` 等 tag 轉成 Excel specification 欄使用的 GD&T 符號。
- `[M]` 轉成 `(M)`。
- `Ø`、`⌀`、`∅`、`ø` 等直徑符號正規化為 `Ø`。
- `\pm`、`/pm`、`$ \pm0.02 $` 轉成 `±0.02`。
- `\mu`、`/mu` 轉成 `µ`。
- LaTeX 上下標公差，例如 `^{+0.05}`、`_{0}^{3+0.01}`，會攤平成 parser 可處理的 unilateral tolerance。
- `17.9 0/-0.04`、`0.50/-0.04` 會拆成 specification 與 tolerance。
- 單邊公差輸出一定補齊正負兩側：`+0.2` 會輸出 `+0.2/-0`，`-0.2` 會輸出 `+0/-0.2`。`±0.2` 本身已表示正負兩側，維持 `±0.2`。

例子：

```text
[GD_POSITION] 0.02 A B C  ->  ⌖ 0.02 A B C
6X Ø 0.3 $ \pm 0.05 $    ->  specification: 6X Ø0.3, tolerance: ±0.05
17.9 0/-0.04             ->  specification: 17.9, tolerance: +0/-0.04
2X 1.5 +0.02/0           ->  specification: 2X1.5, tolerance: +0.02/-0
```

## Symbol Classifier

到新裝置後，`symbol-classifierdata/output/` 通常不會帶過去，需重新建立 split、檢查資料、訓練與評估。

建立 grouped split：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\build_grouped_splits.py
```

檢查資料：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\train_classifier.py --check-data --batch-size 32
```

訓練：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\train_classifier.py `
  --data-root .\symbol-classifierdata `
  --splits-dir .\symbol-classifierdata\splits `
  --output-dir .\symbol-classifierdata\output `
  --model resnet18 `
  --epochs 30 `
  --batch-size 32
```

評估：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\evaluate_classifier.py `
  --data-root .\symbol-classifierdata `
  --split .\symbol-classifierdata\splits\test.csv `
  --checkpoint .\symbol-classifierdata\output\best.pt `
  --threshold 0.90
```

## 驗證

修改 `src/` 後處理、`final-table/output/*.json`、`template.xls`、`example.xls` 或 tolerance profile 後，至少跑：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_workflow.py --no-snapshot
```

成功時 summary 應為：

```json
{
  "status": "success"
}
```

且每個 processed table 的 `workbook_validation.status` 應為 `success`。

目前已用現有 5 個 JSON 驗證過：

```text
final-table/output/test/MIP_filled.xls
final-table/output/test2/MIP_filled.xls
final-table/output/test3/MIP_filled.xls
final-table/output/test4/MIP_filled.xls
final-table/output/test5/MIP_filled.xls
```

## 直徑符號

`ocr_final.py` 與後處理都會盡量把 GLM-OCR 常見的直徑符號變體正規化成 `Ø`。

如果 OCR 完全漏掉直徑符號，純文字後處理無法可靠補回。此情況應回到圖片或 classifier/偵測器檢查，不要把 `DIAMETER` 混回 GD characteristic classifier。

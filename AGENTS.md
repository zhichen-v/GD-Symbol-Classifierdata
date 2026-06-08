# Project Instructions

這份檔案給 Codex 讀。回覆使用繁體中文，先講假設與風險，做最小可驗證改動，不要重構無關程式。

## 工作原則

- 寫程式、review、重構時套用 `karpathy-guidelines`：假設講清楚、改動精準、成功條件可驗證。
- 架構/符號/呼叫關係問題先用 CodeGraph；文字搜尋再用 `rg`。
- 手動改檔用 `apply_patch`。不要覆蓋、移動、刪除使用者資料；不要 revert 非自己造成的變更。
- 多步驟工作要跑最窄的相關檢查，並把失敗原因講清楚。
- 使用者是 fine-tuning 新手；關鍵步驟要解釋，但可安全自動執行的事直接做。

## 目前正式流程

目標是把 GD&T 表格圖片轉成最終 MIP Excel。

- `ocr_final.py`：對完整表格做 cell-by-cell GLM-OCR；GD/GD&T/命名 GD 特徵列不再做 GD parser/classifier，改擷取完整 feature-control-frame 圖片，輸出 `<stem>_image_assets.json` 與 `<stem>_assets/*.png`。
- 表面粗糙度、一般尺寸等非 GD characteristic 不應截圖，維持文字 OCR 與後處理 parser。
- `src/glm_table_adapter.py`：把 OCR table JSON 和 sidecar image assets 轉成 extraction schema。
- `src/run_pipeline.py`：有 `specification_image` 的列跳過文字 parser，Excel specification 由圖片呈現。
- `src/fill_template.py`：把 GD frame 圖片轉成 BMP 並貼進 `template.xls` 的 Specification 欄。圖片縮放常數是 `SPECIFICATION_IMAGE_CELL_SCALE`。
- `src/run_workflow.py`：只處理來源 table JSON，排除 `_image_assets.json`。
- `frontend/worker.py`：對每個 frontend job 暫時指定 `ocr_final.INPUT_DIR` / `OUTPUT_DIR`，不寫入根目錄 `final-table`。

## 環境

- 一般 OCR draft：`.venv` / `uv run ocr.py`。
- final-table OCR、Excel workflow、資料檢查、classifier 相關工作：`.venv-finetune`。
- Base model：`zai-org/GLM-OCR`，優先使用本機 Hugging Face cache。
- Excel `.xls` 後處理依賴 `xlrd`、`xlwt`、`xlutils`；snapshot 依賴 Windows Excel COM / `pywin32`，失敗通常只是 preview warning。

## 重要資料邊界

- `ocr-input/<CATEGORY>/`、`ocr-output/<CATEGORY>/`、`finetune/labels/<CATEGORY>/`：訓練資料與人工標籤。
- `final-table/input/`：正式 full-table 圖片。
- `final-table/output/`：正式 OCR JSON/Markdown、sidecar assets、Excel workflow 輸出；可重建。
- `frontend/runs/<job-id>/`：前端上傳、OCR、Excel 與 preview artifacts；可重建但不要未經確認刪除。
- `finetune/LLaMA-Factory/data/gdt_ocr/`：由 dataset prepare 重建，應排除 `final-table`、top-level 測試圖與 crops。

## 標籤與輸出規則

- 直徑不是 GD characteristic；保留字面 `Ø`，不要輸出 `[DIAMETER]`。
- 最大實體狀態使用 `[M]`。
- 訓練標籤需保留可見數字、小數點、datum 字母、`Ø` 與 modifiers；datum 用大寫。
- Excel 單邊公差要補齊兩側：`+0.2` -> `+0.2/-0`，`-0.2` -> `+0/-0.2`；`±` 保留。
- 自動 label 檢查只代表格式可處理，不代表語意正確。

## 常用命令

OCR 加 Excel：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_ocr_workflow.py --force
```

只用現有 OCR JSON 產 Excel：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_workflow.py --no-snapshot
```

前端服務：

```powershell
.\.venv-finetune\Scripts\python.exe -m uvicorn frontend.app:app --host 127.0.0.1 --port 8000
```

改 `src/`、OCR JSON、template/example、tolerance 規則後至少驗證：

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_workflow.py --no-snapshot
```

成功標準：summary `status: success`，每個 processed table 的 `workbook_validation.status: success`，且產生 `MIP_filled.xls`。

## 訓練安全

- 沒有使用者明確同意，不要開始 smoke/formal training。
- 不要代替使用者建立 `finetune/APPROVED_FOR_TRAINING.txt`。
- `train_lora.ps1` 的 approval guard 必須保留。
- 訓練前要重跑 dataset prepare/check；覆寫既有 output 前先警告。

## 已知狀態

- 目前正式 GD 輸出走 image passthrough，不依賴 LoRA 或 symbol classifier 來判斷 GD 符號。
- 舊 LoRA 不可靠，主要學到 output-token priors；不要把它當成 full-table GD&T classifier。
- classifier 資料與腳本仍可保留作研究/備援，但不是目前 Excel 生產路徑的關鍵依賴。

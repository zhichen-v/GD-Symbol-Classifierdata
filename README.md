# GLM-OCR GD&T Table OCR

此專案目前的主流程是用 `ocr_final.py` 處理 `final-table/input/` 裡的表格圖片，輸出 Markdown 與 JSON 到 `final-table/output/`。

目前已棄用原本的 LoRA 方法。文字與表格 OCR 使用 Hugging Face 上的 `zai-org/GLM-OCR` base model；GD&T characteristic 符號則由 `symbol-classifierdata/` 裡的獨立影像分類器判斷。

## 目前架構

- `ocr_final.py`：正式表格 OCR 入口。
- `final-table/input/`：正式要辨識的表格圖片。
- `final-table/output/`：產生的 `.md` 與 `.json`，不進版本控管。
- `symbol-classifierdata/`：GD&T characteristic 符號分類資料、裁切工具、split、訓練與評估腳本。
- `symbol-classifierdata/output/`：分類器訓練輸出，例如 `best.pt`，不進版本控管；到新裝置後重新訓練。
- `ocr-input/`、`ocr-output/`：舊 OCR/LoRA 訓練資料，暫時保留在本機，但不進版本控管。
- `finetune/`：舊 LoRA 工作區，已不再使用。

重要規則：

- GD&T characteristic 輸出為 `[GD_POSITION]`、`[GD_FLATNESS]` 這類 tag。
- 最大實體狀態輸出 `[M]`。
- 直徑不是 GD characteristic；要保留為字面符號 `Ø`，不要輸出 `[DIAMETER]`。

## 安裝環境

建議在專案根目錄建立虛擬環境：

```powershell
cd C:\Users\<user>\Desktop\model
py -m venv .venv-finetune
.\.venv-finetune\Scripts\python.exe -m ensurepip --upgrade
.\.venv-finetune\Scripts\python.exe -m pip install --upgrade pip
```

依照 `zai-org/GLM-OCR` 的 Hugging Face Transformers 用法，GLM-OCR 需要安裝較新的 Transformers：

```powershell
.\.venv-finetune\Scripts\python.exe -m pip install -r requirements.txt
```

官方模型頁：

<https://huggingface.co/zai-org/GLM-OCR#transformers>

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

## 重建 Symbol Classifier

到新裝置後，`symbol-classifierdata/output/` 不會帶過去，需重新建立 split、檢查資料、訓練與評估。

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

分類器細節與資料裁切方式請看 `symbol-classifierdata/README.md`。

## 執行正式 OCR

確認 `symbol-classifierdata/output/best.pt` 已存在後：

```powershell
.\.venv-finetune\Scripts\python.exe .\ocr_final.py --force
```

輸入：

```text
final-table/input/
```

輸出：

```text
final-table/output/
```

`ocr_final.py` 會用 base GLM-OCR 做文字/表格辨識，再對 generic GD/GD&T 欄位呼叫 symbol classifier。若分類器信心不足或判斷為 `UNKNOWN`，會輸出 `[GD_REVIEW_REQUIRED]`，避免把不確定符號硬轉成錯誤 GD tag。

## 直徑符號

目前 `ocr_final.py` 會把 GLM-OCR 常見的 `\varnothing`、`\diameter`、`\oslash`、`⌀`、`∅`、`ø` 正規化成 `Ø`。

如果 OCR 完全漏掉直徑符號，純文字後處理無法可靠補回。下一步應另外做一個小型直徑符號偵測器，專門處理尺寸欄位開頭的 `Ø`，不要把 `DIAMETER` 混回 GD characteristic classifier。

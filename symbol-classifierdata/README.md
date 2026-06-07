# Symbol Classifier 資料工具

此資料夾包含兩支訓練資料處理腳本：

- `crop_symbol_sheet.py`：將規則排列的符號表裁切成獨立圖片。
- `augment_low_resolution.py`：將每類的 `001.png` 至 `100.png` 製作成明顯降解析度的 `101.png` 至 `200.png`。

請從專案根目錄執行指令：

```powershell
cd C:\Users\<user>\Desktop\model
```

腳本使用 `.venv-finetune`：

```powershell
.\.venv-finetune\Scripts\python.exe
```

## 資料夾結構

每個分類使用獨立資料夾：

```text
symbol-classifierdata/
├── ANGULARITY/
│   ├── 001.png
│   └── ...
├── CYLINDRICITY/
├── PERPENDICULARITY/
├── crop_symbol_sheet.py
└── augment_low_resolution.py
```

執行裁切前，目標分類資料夾必須已存在。

## 裁切符號表

基本格式：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\crop_symbol_sheet.py <來源圖片> <分類名稱> [參數]
```

### 無編號符號表

目前使用的符號表沒有格內編號，必須加入 `--no-numbering`，避免裁掉符號上方。

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\crop_symbol_sheet.py "C:\Users\爸爸\Downloads\PERPENDICULARITY.png" PERPENDICULARITY --no-numbering
```

腳本會自動偵測格線，將圖片依閱讀順序裁切，並輸出成：

```text
001.png
002.png
003.png
...
```

### 指定列數與欄數

如果已知符號表是 `10 × 10`，建議明確指定：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\crop_symbol_sheet.py "C:\Users\爸爸\Downloads\PERPENDICULARITY.png" PERPENDICULARITY --rows 10 --columns 10 --no-numbering
```

若淡格線無法正確偵測，但每格大小完全相等，可使用等分裁切：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\crop_symbol_sheet.py "C:\Users\爸爸\Downloads\PERPENDICULARITY.png" PERPENDICULARITY --rows 10 --columns 10 --no-numbering --equal-grid
```

### 常用裁切參數

| 參數 | 說明 | 預設值 |
|---|---|---:|
| `--rows` | 預期列數 | 自動偵測 |
| `--columns` | 預期欄數 | 自動偵測 |
| `--no-numbering` | 圖片沒有格內編號時使用 | 關閉 |
| `--equal-grid` | 不偵測格線，直接平均分格 | 關閉 |
| `--size` | 每張輸出圖片的寬與高 | `128` |
| `--content-ratio` | 符號占輸出圖片的最大比例 | `0.7` |
| `--threshold` | 判定符號深色像素的門檻 | `160` |
| `--overwrite` | 覆寫目標分類內既有編號圖片 | 關閉 |

預設不會覆寫既有圖片。只有確認要取代舊資料時才加入：

```powershell
--overwrite
```

查看所有參數：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\crop_symbol_sheet.py --help
```

## 製作降解析度資料

`augment_low_resolution.py` 會讀取每個分類的：

```text
001.png 至 100.png
```

每張圖片會隨機縮小至較低解析度，再放大回原始尺寸，輸出為：

```text
101.png 至 200.png
```

預設隨機縮小後的最長邊為 `20` 至 `48` 像素，因此失真與像素化會相當明顯。

### 先測試單一分類

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\augment_low_resolution.py --category PERPENDICULARITY
```

此指令只會處理：

```text
symbol-classifierdata/PERPENDICULARITY/
```

不會修改其他分類。

### 處理所有分類

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\augment_low_resolution.py
```

批次執行前，所有分類都必須完整包含 `001.png` 至 `100.png`。只要任一分類缺圖，腳本會在寫入前停止，不會留下部分輸出。

### 固定隨機結果

加入 `--seed` 可在重新執行時產生相同結果：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\augment_low_resolution.py --category PERPENDICULARITY --seed 7
```

### 調整降解析度程度

數值越小，失真越明顯。以下會將圖片隨機縮至最長邊 `12` 至 `32` 像素：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\augment_low_resolution.py --category PERPENDICULARITY --min-side 12 --max-side 32
```

### 重新產生既有輸出

如果 `101.png` 至 `200.png` 已存在，腳本預設會停止，避免意外覆寫。

確認要重新產生時：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\augment_low_resolution.py --category PERPENDICULARITY --overwrite
```

查看所有參數：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\augment_low_resolution.py --help
```

## 建議操作流程

1. 確認分類資料夾已建立。
2. 使用 `crop_symbol_sheet.py` 產生 `001.png` 至 `100.png`。
3. 人工快速檢查裁切內容與分類是否正確。
4. 使用 `augment_low_resolution.py --category <分類>` 測試單一分類。
5. 確認效果後，再執行全分類降解析度。

自動檢查只能確認檔案數量、命名與基本影像條件，不能證明符號分類在語意上正確。

## 建立分類器 split

`build_grouped_splits.py` 會建立正式訓練用的類別清單與 grouped split：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\build_grouped_splits.py
```

輸出：

```text
symbol-classifierdata/splits/classes.txt
symbol-classifierdata/splits/manifest.csv
symbol-classifierdata/splits/train.csv
symbol-classifierdata/splits/val.csv
symbol-classifierdata/splits/test.csv
```

同一來源 group，例如 `001.png` 與低解析版本 `101.png`，會被放在同一個 split，避免驗證資料洩漏。

## 訓練 GD 符號分類器

正式訓練前先跑資料檢查：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\train_classifier.py --check-data --batch-size 32
```

開始訓練：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\train_classifier.py `
  --data-root .\symbol-classifierdata `
  --splits-dir .\symbol-classifierdata\splits `
  --output-dir .\symbol-classifierdata\output `
  --model resnet18 `
  --epochs 30 `
  --batch-size 32
```

輸出：

```text
symbol-classifierdata/output/best.pt
symbol-classifierdata/output/train_metrics.json
```

預設不下載 torchvision 預訓練權重。若確認環境可下載或已有快取，可加入 `--pretrained`。

## 評估分類器

訓練完成後，用 test split 評估：

```powershell
.\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\evaluate_classifier.py `
  --data-root .\symbol-classifierdata `
  --split .\symbol-classifierdata\splits\test.csv `
  --checkpoint .\symbol-classifierdata\output\best.pt `
  --threshold 0.90
```

輸出：

```text
symbol-classifierdata/output/evaluation/metrics.json
symbol-classifierdata/output/evaluation/predictions.csv
symbol-classifierdata/output/evaluation/confusion_matrix_raw.csv
symbol-classifierdata/output/evaluation/confusion_matrix_gated.csv
```

先看 `metrics.json` 和 confusion matrix。若 `PARALLELISM`、`FLATNESS`、`CIRCULARITY`、`POSITION` 仍互相混淆，或 `UNKNOWN` precision 不夠，先補資料與調整門檻，不要接進 `ocr_final.py`。

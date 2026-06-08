# Project Instructions

## Engineering Workflow

- State assumptions and surface meaningful ambiguity before consequential changes.
- Prefer the smallest change that solves the requested problem. Do not refactor unrelated code.
- Preserve user data and existing changes. Do not overwrite, move, or delete source material without verifying the exact paths and collision risk.
- Use Codegraph first for codebase behavior and architecture when the index is available.
- For multi-step work, define verifiable success criteria and continue through implementation and checks.
- Use `apply_patch` for manual file edits and run the narrowest relevant verification afterward.

## Communication

- Use Traditional Chinese when communicating with the user.
- The user is new to fine-tuning. Explain consequential steps clearly, but perform discoverable and safe work proactively.
- Do not treat automated label checks as proof that labels are semantically correct.

## Project Goal

This project uses `zai-org/GLM-OCR` plus a dedicated closed-set GD&T symbol classifier to read full GD&T tables, normalize callouts, and write the final MIP Excel workbook.

- GD&T characteristic symbols use tags such as `[GD_POSITION]` and `[GD_FLATNESS]`.
- Maximum material condition uses `[M]`.
- Diameter is not a GD characteristic. Preserve it as the literal character `Ø`; do not output `[DIAMETER]`.

## Environment

- Run normal OCR with `.venv` through `uv run ocr.py`.
- Run data preparation, review, checks, classifier work, final-table OCR, and Excel post-processing with `.venv-finetune`.
- Base model: `zai-org/GLM-OCR`, available in the local Hugging Face cache.
- Fine-tuning framework: `finetune/LLaMA-Factory`.
- Target GPU: NVIDIA GeForce RTX 4070 SUPER 12 GiB with BF16 support.
- Excel post-processing depends on `xlrd`, `xlwt`, `xlutils`, and optional Windows Excel COM snapshot support through `pywin32`.

## Data Boundaries

- `ocr-input/<CATEGORY>/`: only categorized local training screenshots.
- `ocr-output/<CATEGORY>/`: base-model OCR drafts.
- `finetune/labels/<CATEGORY>/`: human-corrected training labels.
- `final-table/`: reserved full-table images and labels; exclude from the current training dataset.
- `finetune/LLaMA-Factory/data/gdt_ocr/`: generated dataset images. It is rebuilt by `prepare_dataset.ps1`.
- `finetune/output/gdt-lora/`: formal LoRA output; currently empty and ready for training.
- `finetune/output/gdt-lora-old/`: verified backup of the old pipeline-proof adapter; it does not represent the current reviewed dataset.
- `ocr_final.py`: processes every image under `final-table/input`, crops cells only in memory, and writes only final Markdown/JSON files to `final-table/output`.
- `ocr_final.py` defaults to safe mode. Named characteristics such as `FLATNESS` map deterministically to tags. Generic `GD`/`GD&T` rows use the dedicated symbol classifier when available; rejected rows output `[GD_REVIEW_REQUIRED]`.
- `src/`: production post-processing tools copied/adapted from the old `GDT-Extraction-FB` project. Keep workflow scripts and helper modules here.
- `src/run_workflow.py`: consumes existing `final-table/output/*.json` table OCR results and writes final MIP Excel workbooks.
- `src/run_ocr_workflow.py`: runs `ocr_final.py` first, then runs the JSON-to-Excel workflow.
- `final-table/output/<TABLE_STEM>/`: generated normalized extraction JSON, debug JSON, and `MIP_filled.xls` workbooks. These are rebuildable outputs derived from `final-table/output/<TABLE_STEM>.json`.

`prepare_dataset.py` intentionally includes only folders listed in its `CATEGORY_TAGS`. It excludes `final-table`, top-level images, and the old `symbol_crops.json` samples.

## Current Reviewed Dataset

As of 2026-06-07:

- Human-reviewed categorized images: 157
- Train samples: 126
- Eval samples: 31
- Automated label review: 157 ready, 0 flagged
- `finetune/APPROVED_FOR_TRAINING.txt`: absent

Current categories:

`ANGULARITY`, `CIRCULARITY`, `CIRCULAR_RUNOUT`, `CONCENTRICITY`, `CYLINDRICITY`, `DIAMETER`, `FLATNESS`, `M`, `PARALLELISM`, `PERPENDICULARITY`, `POSITION`, `PROFILE_LINE`, `PROFILE_SURFACE`, `SYMMETRY`, `TOTAL_RUNOUT`.

## Label Rules

- Labels must exactly preserve visible numbers, decimal points, datum letters, `Ø`, and modifiers.
- Remove model special tokens, LaTeX, duplicate symbols, and invented content.
- Use uppercase datum letters.
- Use the stable tag matching the image category.
- For `DIAMETER`, the answer must contain literal `Ø`, for example `Ø1` or `Ø0.03`.
- For a complete frame, keep every visible element, for example `[GD_POSITION] Ø0.1 [M] A B C`.
- If an image is in the wrong category, move the image, OCR output, and corrected label together. Avoid overwriting an existing filename.

## Final Excel Tolerance Rules

- Final Excel tolerance output must show both positive and negative tolerance sides for unilateral tolerances.
- Preserve `±` for bilateral tolerances because it already represents both positive and negative sides.
- If only a positive tolerance is present, append the zero negative side, for example `+0.2/-0`.
- If only a negative tolerance is present, prepend the zero positive side, for example `+0/-0.2`.

## Standard Data Workflow

```powershell
uv run ocr.py
.\.venv-finetune\Scripts\python.exe .\finetune\scripts\prepare_dataset.py --init-drafts
.\finetune\scripts\review_ui.ps1
.\finetune\scripts\review_labels.ps1
.\finetune\scripts\prepare_dataset.ps1
.\finetune\scripts\check_finetune.ps1
```

Do not use `--refresh-drafts` after manual review unless the user explicitly requests replacing reviewed labels.

## Training Safety

- Never start smoke training or formal training without explicit user approval in the current conversation.
- Never create `finetune/APPROVED_FOR_TRAINING.txt` on the user's behalf unless they explicitly authorize training.
- `train_lora.ps1` requires the approval marker. Keep this guard intact.
- Re-run `prepare_dataset.ps1` and `check_finetune.ps1` immediately before any approved training.
- Training configs overwrite their output directories. Warn before replacing the existing old proof adapter.

## Verification

After changing source images, labels, category mappings, or dataset preparation:

```powershell
.\finetune\scripts\review_labels.ps1
.\finetune\scripts\prepare_dataset.ps1
.\finetune\scripts\check_finetune.ps1
```

Confirm generated JSON contains no `final-table`, top-level `test*.png`, or `/crops/` entries.

`evaluate_crops.ps1` is currently not applicable because the old generated crop dataset is intentionally excluded. `evaluate_lora.ps1` currently reads only top-level files in its input directory, so do not present it as recursive category evaluation.

For full grid tables, prefer the cell-by-cell GLM-OCR plus classifier pipeline:

```powershell
.\.venv-finetune\Scripts\python.exe .\ocr_final.py
```

This workflow is the current minimum viable solution for full-table GD&T recognition. It assumes visible straight grid lines. Direct whole-image LoRA inference does not reliably emit GD&T tags because the symbols become too small at full-table scale.

After `final-table/output/*.json` exists, generate final Excel files with:

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_workflow.py --no-snapshot
```

To run OCR and Excel generation together:

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_ocr_workflow.py --no-snapshot
```

Use `--no-snapshot` for deterministic automated checks. Snapshot generation depends on local Excel COM automation and may fail with a warning even when the workbook is valid.

After changing `src/` post-processing, `final-table/output/*.json`, template files, or tolerance rules, verify with:

```powershell
.\.venv-finetune\Scripts\python.exe .\src\run_workflow.py --no-snapshot
```

The workflow must report `status: success`, workbook validation must pass, and each processed table should produce `final-table/output/<TABLE_STEM>/MIP_filled.xls`.

The current LoRA is not a reliable GD&T symbol classifier. Its training used the LLaMA-Factory defaults `freeze_vision_tower: true` and `freeze_multi_modal_projector: true`, so it mainly learned output-token priors rather than new visual distinctions. The production path is a dedicated closed-set classifier operating on complete first feature-control-frame compartments, with an unknown/reject class and calibrated confidence threshold.

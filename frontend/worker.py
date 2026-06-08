from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


FRONTEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = FRONTEND_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Run the project OCR and Excel workflow for one frontend job.")
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--ocr-output-dir", type=Path, required=True)
    parser.add_argument("--workflow-output-dir", type=Path, required=True)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--download-base-model", action="store_true")
    parser.add_argument("--symbol-classifier-threshold", type=float, default=0.90)
    parser.add_argument("--symbol-classifier-device", default="auto")
    parser.add_argument("--disable-symbol-classifier", action="store_true")
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument("--snapshot-range")
    args = parser.parse_args(argv)

    args.job_dir.mkdir(parents=True, exist_ok=True)
    try:
        summary = run_frontend_workflow(args)
    except Exception as exc:
        summary = {
            "status": "errors_found",
            "errors": [str(exc)],
            "warnings": [],
            "workflow": None,
        }
        _write_summary(args.job_dir, summary)
        print(f"FAILED: {exc}", file=sys.stderr, flush=True)
        return 1

    _write_summary(args.job_dir, summary)
    return 0 if summary["status"] == "success" else 1


def run_frontend_workflow(args: argparse.Namespace) -> dict[str, Any]:
    _prepare_imports()
    image_paths = _image_paths(args.input_dir)
    if not image_paths:
        raise FileNotFoundError(f"No table images found in {args.input_dir}")

    emit_progress(12, f"OCR 準備中（{len(image_paths)} 張表格）")
    ocr_code = _run_ocr_final(args)
    if ocr_code != 0:
        raise RuntimeError(f"ocr_final.py failed with code {ocr_code}")

    json_paths = _workflow_json_paths(args.ocr_output_dir)
    if not json_paths:
        raise FileNotFoundError(f"OCR did not create JSON files in {args.ocr_output_dir}")

    emit_progress(72, f"OCR 完成，正在寫入 {len(json_paths)} 份 Excel")
    from run_workflow import run_workflow

    workflow = run_workflow(
        inputs=[str(path) for path in json_paths],
        output_root=str(args.workflow_output_dir),
        template_path=str(PROJECT_ROOT / "template.xls"),
        example_path=str(PROJECT_ROOT / "example.xls"),
        tolerance_profile_path=str(PROJECT_ROOT / "data" / "tolerance_profile.json"),
        default_unit="auto",
        create_snapshot=not args.no_snapshot,
        snapshot_sheet="MIP Results",
        snapshot_range=args.snapshot_range,
        validate_workbook=True,
    )

    emit_progress(92, "正在整理 Excel 預覽圖")
    _attach_previews(workflow)

    return {
        "status": workflow.get("status", "errors_found"),
        "ocr_json_count": len(json_paths),
        "workflow": workflow,
        "errors": workflow.get("errors", []),
        "warnings": workflow.get("warnings", []),
    }


def _run_ocr_final(args: argparse.Namespace) -> int:
    import ocr_final

    ocr_final.INPUT_DIR = args.input_dir.resolve()
    ocr_final.OUTPUT_DIR = args.ocr_output_dir.resolve()

    ocr_args = [
        "ocr_final.py",
        "--force",
        "--max-new-tokens",
        str(args.max_new_tokens),
        "--symbol-classifier-threshold",
        str(args.symbol_classifier_threshold),
        "--symbol-classifier-device",
        args.symbol_classifier_device,
    ]
    if args.download_base_model:
        ocr_args.append("--download-base-model")
    if args.disable_symbol_classifier:
        ocr_args.append("--disable-symbol-classifier")

    previous_argv = sys.argv[:]
    try:
        sys.argv = ocr_args
        return int(ocr_final.main())
    finally:
        sys.argv = previous_argv


def _attach_previews(workflow: dict[str, Any]) -> None:
    for result in workflow.get("results", []):
        workbook_path = Path(result.get("workbook", ""))
        snapshot = result.get("snapshot") or {}
        snapshot_path = Path(snapshot.get("output_path", ""))
        if snapshot.get("status") == "success" and snapshot_path.is_file():
            result["frontend_preview"] = {
                "status": "success",
                "source": "excel_snapshot",
                "output_path": str(snapshot_path),
            }
            continue

        preview_path = workbook_path.with_name("MIP_filled_preview.png")
        try:
            _render_workbook_preview(workbook_path, preview_path)
        except Exception as exc:
            result["frontend_preview"] = {
                "status": "failed",
                "source": "fallback_renderer",
                "warning": str(exc),
            }
            continue
        result["frontend_preview"] = {
            "status": "success",
            "source": "fallback_renderer",
            "output_path": str(preview_path),
        }


def _render_workbook_preview(workbook_path: Path, output_path: Path) -> None:
    import xlrd
    from PIL import Image, ImageDraw, ImageFont

    if not workbook_path.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")

    book = xlrd.open_workbook(str(workbook_path), formatting_info=False)
    try:
        sheet = book.sheet_by_name("MIP Results")
    except xlrd.XLRDError:
        sheet = book.sheet_by_index(0)

    min_rows = 26
    min_cols = 19
    max_rows = 34
    max_cols = 19
    row_count = min(max(sheet.nrows, min_rows), max_rows)
    col_count = min(max(sheet.ncols, min_cols), max_cols)
    font = _load_font(14)
    bold_font = _load_font(15, bold=True)

    values = [
        [_cell_text(sheet.cell(row, col)) if row < sheet.nrows and col < sheet.ncols else "" for col in range(col_count)]
        for row in range(row_count)
    ]
    col_widths = _column_widths(values, font)
    row_height = 34
    title_height = 46
    margin = 22
    width = sum(col_widths) + margin * 2
    height = title_height + row_height * row_count + margin

    image = Image.new("RGB", (width, height), "#f6f3ea")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 6), fill="#0f2f2f")
    draw.text((margin, 17), "MIP Results", fill="#111827", font=bold_font)

    y = title_height
    for row_index, row in enumerate(values):
        x = margin
        for col_index, value in enumerate(row):
            cell_width = col_widths[col_index]
            fill = "#ffffff"
            text_fill = "#152323"
            if row_index < 2:
                fill = "#173f3f"
                text_fill = "#f7fbf8"
            elif row_index % 2:
                fill = "#fbfaf6"
            draw.rectangle((x, y, x + cell_width, y + row_height), fill=fill, outline="#b9c1bd")
            clipped = _clip_text(draw, value, font, max(10, cell_width - 12))
            draw.text((x + 6, y + 9), clipped, fill=text_fill, font=font)
            x += cell_width
        y += row_height

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")


def _column_widths(values: list[list[str]], font: Any) -> list[int]:
    from PIL import Image, ImageDraw

    scratch = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(scratch)
    widths = []
    for col in range(len(values[0])):
        samples = [row[col] for row in values[:12]]
        measured = 54
        for sample in samples:
            if not sample:
                continue
            box = draw.textbbox((0, 0), sample, font=font)
            measured = max(measured, box[2] - box[0] + 18)
        widths.append(min(max(measured, 58), 170))
    return widths


def _clip_text(draw: Any, value: str, font: Any, max_width: int) -> str:
    if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
        return value
    text = value
    while text and draw.textbbox((0, 0), text + "...", font=font)[2] > max_width:
        text = text[:-1]
    return (text + "...") if text else ""


def _cell_text(cell: Any) -> str:
    import xlrd

    if cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
        return ""
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        value = float(cell.value)
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return str(cell.value).replace("\n", " ").strip()


def _load_font(size: int, bold: bool = False) -> Any:
    from PIL import ImageFont

    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / ("arialbd.ttf" if bold else "arial.ttf"),
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / ("calibrib.ttf" if bold else "calibri.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _prepare_imports() -> None:
    for path in (str(PROJECT_ROOT), str(SRC_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _image_paths(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def _workflow_json_paths(ocr_output_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in ocr_output_dir.glob("*.json")
        if path.is_file() and not path.name.lower().endswith("_image_assets.json")
    )


def emit_progress(progress: int, step: str, status: str = "running") -> None:
    print(
        "FRONTEND_PROGRESS "
        + json.dumps({"progress": progress, "step": step, "status": status}, ensure_ascii=False),
        flush=True,
    )


def _write_summary(job_dir: Path, summary: dict[str, Any]) -> None:
    target = job_dir / "worker-summary.json"
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

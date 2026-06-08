import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from run_workflow import DEFAULT_INPUT_GLOB, DEFAULT_OUTPUT_ROOT, run_workflow
except ImportError:
    from .run_workflow import DEFAULT_INPUT_GLOB, DEFAULT_OUTPUT_ROOT, run_workflow


ROOT = Path(__file__).resolve().parents[1]


def run_ocr_workflow(
    ocr_python=None,
    force=False,
    max_new_tokens=128,
    download_base_model=False,
    symbol_classifier_checkpoint=None,
    symbol_classifier_threshold=0.90,
    symbol_classifier_device="auto",
    disable_symbol_classifier=False,
    inputs=None,
    input_glob=DEFAULT_INPUT_GLOB,
    output_root=DEFAULT_OUTPUT_ROOT,
    template_path="template.xls",
    example_path="example.xls",
    tolerance_profile_path="data/tolerance_profile.json",
    default_unit="auto",
    create_snapshot=True,
    snapshot_sheet="MIP Results",
    snapshot_range=None,
    validate_workbook=True,
):
    ocr_result = _run_ocr_final(
        ocr_python=ocr_python,
        force=force,
        max_new_tokens=max_new_tokens,
        download_base_model=download_base_model,
        symbol_classifier_checkpoint=symbol_classifier_checkpoint,
        symbol_classifier_threshold=symbol_classifier_threshold,
        symbol_classifier_device=symbol_classifier_device,
        disable_symbol_classifier=disable_symbol_classifier,
    )
    if ocr_result["returncode"] != 0:
        return {
            "status": "errors_found",
            "ocr": ocr_result,
            "workflow": None,
            "errors": ["ocr_final.py failed; Excel workflow was not started."],
            "warnings": [],
        }

    workflow = run_workflow(
        inputs=inputs,
        input_glob=input_glob,
        output_root=output_root,
        template_path=template_path,
        example_path=example_path,
        tolerance_profile_path=tolerance_profile_path,
        default_unit=default_unit,
        create_snapshot=create_snapshot,
        snapshot_sheet=snapshot_sheet,
        snapshot_range=snapshot_range,
        validate_workbook=validate_workbook,
    )
    return {
        "status": workflow["status"],
        "ocr": ocr_result,
        "workflow": workflow,
        "errors": workflow.get("errors", []),
        "warnings": workflow.get("warnings", []),
    }


def _run_ocr_final(
    ocr_python,
    force,
    max_new_tokens,
    download_base_model,
    symbol_classifier_checkpoint,
    symbol_classifier_threshold,
    symbol_classifier_device,
    disable_symbol_classifier,
):
    command = [str(ocr_python or sys.executable), str(ROOT / "ocr_final.py")]
    if force:
        command.append("--force")
    if download_base_model:
        command.append("--download-base-model")
    if disable_symbol_classifier:
        command.append("--disable-symbol-classifier")
    command.extend(["--max-new-tokens", str(max_new_tokens)])
    command.extend(["--symbol-classifier-threshold", str(symbol_classifier_threshold)])
    command.extend(["--symbol-classifier-device", str(symbol_classifier_device)])
    if symbol_classifier_checkpoint:
        command.extend(["--symbol-classifier-checkpoint", str(symbol_classifier_checkpoint)])

    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _configure_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def main(argv=None):
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Run ocr_final.py, then fill MIP Excel workbooks from its JSON output.")
    parser.add_argument("--ocr-python", help="Python executable used to run ocr_final.py. Defaults to this interpreter.")
    parser.add_argument("--force", action="store_true", help="Regenerate OCR JSON/Markdown before Excel processing.")
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--download-base-model", action="store_true")
    parser.add_argument("--symbol-classifier-checkpoint")
    parser.add_argument("--symbol-classifier-threshold", type=float, default=0.90)
    parser.add_argument("--symbol-classifier-device", default="auto")
    parser.add_argument("--disable-symbol-classifier", action="store_true")
    parser.add_argument("--input", action="append", help="Input final-table JSON path for the Excel step.")
    parser.add_argument("--input-glob", default=DEFAULT_INPUT_GLOB)
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--template", default="template.xls")
    parser.add_argument("--example", default="example.xls")
    parser.add_argument("--tolerance-profile", default="data/tolerance_profile.json")
    parser.add_argument("--default-unit", default="auto", choices=("auto", "inch", "metric", "mm", "in"))
    parser.add_argument("--snapshot-sheet", default="MIP Results")
    parser.add_argument("--snapshot-range")
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument("--no-validate", action="store_true")
    args = parser.parse_args(argv)

    try:
        summary = run_ocr_workflow(
            ocr_python=args.ocr_python,
            force=args.force,
            max_new_tokens=args.max_new_tokens,
            download_base_model=args.download_base_model,
            symbol_classifier_checkpoint=args.symbol_classifier_checkpoint,
            symbol_classifier_threshold=args.symbol_classifier_threshold,
            symbol_classifier_device=args.symbol_classifier_device,
            disable_symbol_classifier=args.disable_symbol_classifier,
            inputs=args.input,
            input_glob=args.input_glob,
            output_root=args.output_root,
            template_path=args.template,
            example_path=args.example,
            tolerance_profile_path=args.tolerance_profile,
            default_unit=args.default_unit,
            create_snapshot=not args.no_snapshot,
            snapshot_sheet=args.snapshot_sheet,
            snapshot_range=args.snapshot_range,
            validate_workbook=not args.no_validate,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        summary = {
            "status": "errors_found",
            "ocr": None,
            "workflow": None,
            "errors": [str(exc)],
            "warnings": [],
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

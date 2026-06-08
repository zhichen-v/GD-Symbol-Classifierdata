import argparse
import glob
import json
import sys
from pathlib import Path

try:
    from extraction_schema import ExtractionValidationError
    from glm_table_adapter import convert_table_json_file
    from run_pipeline import run_pipeline
    from validate_output import validate_output
except ImportError:
    from .extraction_schema import ExtractionValidationError
    from .glm_table_adapter import convert_table_json_file
    from .run_pipeline import run_pipeline
    from .validate_output import validate_output


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_GLOB = "final-table/output/*.json"
DEFAULT_OUTPUT_ROOT = "final-table/output"


def run_workflow(
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
    table_paths = _resolve_inputs(inputs, input_glob)
    results = []

    for table_path in table_paths:
        paths = _paths_for_input(table_path, output_root)
        paths["output_dir"].mkdir(parents=True, exist_ok=True)

        _, extraction_report = convert_table_json_file(
            table_path,
            output_path=paths["extraction"],
            default_unit=default_unit,
        )
        debug = run_pipeline(
            extraction_path=str(paths["extraction"]),
            template_path=str(_resolve_project_path(template_path)),
            output_path=str(paths["workbook"]),
            debug_path=str(paths["debug"]),
            tolerance_profile_path=str(_resolve_project_path(tolerance_profile_path))
            if tolerance_profile_path and _resolve_project_path(tolerance_profile_path).exists()
            else None,
            example_path=str(_resolve_project_path(example_path)) if example_path else None,
            snapshot_sheet=snapshot_sheet,
            snapshot_range=snapshot_range,
            create_snapshot=create_snapshot,
        )
        workbook_validation = (
            validate_output(
                str(_resolve_project_path(template_path)),
                str(paths["workbook"]),
                str(paths["debug"]),
                example_path=str(_resolve_project_path(example_path)) if example_path else None,
            )
            if validate_workbook
            else {"status": "skipped", "errors": [], "warnings": [], "row_count": len(debug["output_rows"])}
        )

        results.append(
            {
                "input": _display_path(table_path),
                "row_count": len(debug["output_rows"]),
                "extraction": str(paths["extraction"]),
                "workbook": str(paths["workbook"]),
                "debug": str(paths["debug"]),
                "snapshot": debug["metadata"].get("snapshot"),
                "extraction_validation": extraction_report,
                "workbook_validation": workbook_validation,
            }
        )

    return _summary(results)


def _resolve_inputs(inputs, input_glob):
    if inputs:
        paths = [Path(path) for path in inputs]
    else:
        paths = [Path(path) for path in sorted(glob.glob(input_glob))]
    paths = [path for path in paths if _is_source_table_json(path)]

    if not paths:
        raise FileNotFoundError(f"No final-table JSON files found for {input_glob!r}.")
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Input JSON files not found: {missing}")
    return paths


def _is_source_table_json(path):
    name = Path(path).name.lower()
    return name.endswith(".json") and name not in {"extraction_debug.json"} and not name.endswith("_extraction.json")


def _paths_for_input(table_path, output_root):
    stem = Path(table_path).stem
    output_dir = Path(output_root) / stem
    return {
        "output_dir": output_dir,
        "extraction": output_dir / f"{stem}_extraction.json",
        "workbook": output_dir / "MIP_filled.xls",
        "debug": output_dir / "extraction_debug.json",
    }


def _resolve_project_path(path):
    value = Path(path)
    if value.is_absolute():
        return value
    return ROOT / value


def _summary(results):
    errors = []
    warnings = []
    for result in results:
        extraction_validation = result["extraction_validation"]
        workbook_validation = result["workbook_validation"]
        errors.extend(f"{result['input']}: {error}" for error in extraction_validation.get("errors", []))
        errors.extend(f"{result['input']}: {error}" for error in workbook_validation.get("errors", []))
        warnings.extend(f"{result['input']}: {warning}" for warning in extraction_validation.get("warnings", []))
        warnings.extend(f"{result['input']}: {warning}" for warning in workbook_validation.get("warnings", []))
        snapshot = result.get("snapshot") or {}
        if snapshot.get("status") == "failed":
            warnings.append(f"{result['input']}: snapshot failed: {snapshot.get('warning')}")

    return {
        "status": "success" if not errors else "errors_found",
        "processed": len(results),
        "results": results,
        "errors": errors,
        "warnings": warnings,
    }


def _display_path(path):
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _configure_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def main(argv=None):
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Convert final-table/output JSON files into filled MIP Excel workbooks.")
    parser.add_argument("--input", action="append", help="Input final-table JSON path. Repeat for multiple files.")
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
        summary = run_workflow(
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
    except (ExtractionValidationError, FileNotFoundError, OSError, ValueError) as exc:
        summary = exc.report if isinstance(exc, ExtractionValidationError) else {
            "status": "errors_found",
            "errors": [str(exc)],
            "warnings": [],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

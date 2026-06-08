import argparse
import json
import os
import sys
from datetime import datetime, timezone

try:
    from equipment_resolver import resolve_equipment
    from fill_template import fill_template
    from location_parser import parse_location
    from parse_specification import infer_unit_from_specification, parse_specification
    from snapshot_excel import snapshot_workbook
    from tolerance_resolver import resolve_tolerance
except ImportError:
    from .equipment_resolver import resolve_equipment
    from .fill_template import fill_template
    from .location_parser import parse_location
    from .parse_specification import infer_unit_from_specification, parse_specification
    from .snapshot_excel import snapshot_workbook
    from .tolerance_resolver import resolve_tolerance


def run_pipeline(
    extraction_path,
    template_path,
    output_path,
    debug_path,
    tolerance_profile_path=None,
    example_path=None,
    snapshot_path=None,
    snapshot_sheet="MIP Results",
    snapshot_range=None,
    create_snapshot=True,
):
    extraction = _read_json(extraction_path)
    configured_default_unit = extraction.get("default_unit", "auto")
    default_unit = _default_unit(configured_default_unit, extraction.get("rows", []))
    tolerance_profile = _read_json(tolerance_profile_path) if tolerance_profile_path and os.path.exists(tolerance_profile_path) else None

    normalized_rows = []
    output_rows = []

    for item_index, source_row in enumerate(extraction["rows"], start=1):
        parsed = parse_specification(
            source_row.get("raw_specification", ""),
            characteristic=source_row.get("characteristic", ""),
            default_unit=source_row.get("unit", default_unit),
            infer_unit="unit" not in source_row,
        )
        tolerance = resolve_tolerance(parsed, tolerance_profile=tolerance_profile)
        equipment = resolve_equipment(
            source_row.get("characteristic", ""),
            source_row.get("raw_specification", ""),
            parsed=parsed,
        )
        location = parse_location(source_row.get("location", ""))
        warnings = []
        warnings.extend(source_row.get("warnings", []))
        warnings.extend(_effective_parse_warnings(parsed, tolerance))
        warnings.extend(location.get("warnings", []))
        warnings.extend(tolerance.get("missing_evidence", []))

        output_row = {
            "item": item_index,
            "drawing_sheet": location["drawing_sheet"],
            "zone": location["zone"],
            "excel_specification": parsed.get("excel_specification", ""),
            "excel_tolerance": tolerance["final_tolerance"],
            "measuring_equipment": equipment["equipment"],
            "production_section": _production_section(equipment),
            "suqc": "*",
            "ipqc": "\u25cb",
            "ogqc": "\u25ce",
        }

        normalized_rows.append(
            {
                "source_row": source_row,
                "location": location,
                "parsed_specification": parsed,
                "tolerance_decision": tolerance,
                "equipment_decision": equipment,
                "warnings": warnings,
                "output_row": output_row,
            }
        )
        output_rows.append(output_row)

    debug = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "extraction_path": extraction_path,
            "template_path": template_path,
            "example_path": example_path,
            "output_path": output_path,
            "tolerance_profile_path": tolerance_profile_path,
            "default_unit": default_unit,
        },
        "raw_extraction": extraction,
        "normalized_rows": normalized_rows,
        "output_rows": output_rows,
    }

    os.makedirs(os.path.dirname(debug_path) or ".", exist_ok=True)
    _write_json(debug_path, debug)

    fill_template(template_path, output_rows, output_path)
    debug["metadata"]["snapshot"] = _create_snapshot_metadata(
        output_path=output_path,
        snapshot_path=snapshot_path,
        sheet_name=snapshot_sheet,
        cell_range=snapshot_range,
        enabled=create_snapshot,
    )
    _write_json(debug_path, debug)
    return debug


def _production_section(equipment):
    if equipment.get("equipment") == "Visual":
        return "All Process"
    return "MCM"


def _default_unit(configured_default_unit, rows):
    configured = (configured_default_unit or "").strip().lower()
    if configured and configured != "auto":
        return configured

    counts = {"inch": 0, "metric": 0}
    for row in rows:
        unit = infer_unit_from_specification(
            row.get("raw_specification", ""),
            characteristic=row.get("characteristic", ""),
        )
        if unit in counts:
            counts[unit] += 1

    if counts["inch"] or counts["metric"]:
        return "inch" if counts["inch"] >= counts["metric"] else "metric"
    return "inch"


def _effective_parse_warnings(parsed, tolerance):
    warnings = parsed.get("warnings", [])
    if tolerance.get("source") != "missing_general_tolerance_source":
        return [
            warning
            for warning in warnings
            if warning != "No explicit tolerance found; default tolerance source is required."
        ]
    return warnings


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _create_snapshot_metadata(output_path, snapshot_path=None, sheet_name="MIP Results", cell_range=None, enabled=True):
    if not enabled:
        return {"status": "skipped", "enabled": False}

    try:
        result = snapshot_workbook(
            output_path,
            output_path=snapshot_path,
            sheet_name=sheet_name,
            cell_range=cell_range,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "enabled": True,
            "warning": str(exc),
        }

    return {
        "status": "success",
        "enabled": True,
        "output_path": result["output"],
        "sheet": result["sheet"],
        "range": result["range"],
    }


def _configure_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def main(argv=None):
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Run the GDT extraction MVP pipeline.")
    parser.add_argument("--extraction", default="data/test_extraction.json")
    parser.add_argument("--template", default="template.xls")
    parser.add_argument("--output", default="output/MIP_filled.xls")
    parser.add_argument("--debug", default="output/extraction_debug.json")
    parser.add_argument("--tolerance-profile", default="data/tolerance_profile.json")
    parser.add_argument("--example", default="example.xls")
    parser.add_argument("--snapshot-output", help="Optional PNG snapshot path. Defaults beside the output workbook.")
    parser.add_argument("--snapshot-sheet", default="MIP Results", help="Worksheet to snapshot after writing Excel.")
    parser.add_argument("--snapshot-range", help="Optional Excel range to snapshot, e.g. A1:S30.")
    parser.add_argument("--no-snapshot", action="store_true", help="Skip the review PNG snapshot.")
    args = parser.parse_args(argv)

    debug = run_pipeline(
        extraction_path=args.extraction,
        template_path=args.template,
        output_path=args.output,
        debug_path=args.debug,
        tolerance_profile_path=args.tolerance_profile,
        example_path=args.example,
        snapshot_path=args.snapshot_output,
        snapshot_sheet=args.snapshot_sheet,
        snapshot_range=args.snapshot_range,
        create_snapshot=not args.no_snapshot,
    )
    print(
        json.dumps(
            {
                "status": "success",
                "rows": len(debug["output_rows"]),
                "output": args.output,
                "debug": args.debug,
                "snapshot": debug["metadata"].get("snapshot"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

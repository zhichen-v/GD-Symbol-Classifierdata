import argparse
import json
import sys
from pathlib import Path

try:
    from extraction_schema import ExtractionValidationError, normalize_extraction
except ImportError:
    from .extraction_schema import ExtractionValidationError, normalize_extraction


REQUIRED_HEADERS = ("LOCATION", "CHARACTERISTIC", "SPECIFICATION")
OPTIONAL_HEADERS = ("SPC", "ACTUAL", "COMMENTS", "PASS", "FAIL", "INITIAL")
HEADER_ALIASES = {
    "CHAR": "CHARACTERISTIC",
    "FEATURE": "CHARACTERISTIC",
    "SPEC": "SPECIFICATION",
    "REQUIREMENT": "SPECIFICATION",
}


def convert_table_json_file(input_path, output_path=None, default_unit="auto"):
    input_path = Path(input_path)
    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    extraction, report = table_payload_to_extraction(
        payload,
        source_json=_display_path(input_path),
        default_unit=default_unit,
    )
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(extraction, handle, ensure_ascii=False, indent=2)
    return extraction, report


def table_payload_to_extraction(payload, source_json=None, default_unit="auto"):
    if isinstance(payload, dict) and "rows" in payload:
        extraction, report = normalize_extraction(payload, source_image=source_json)
        return extraction, report

    if not isinstance(payload, list) or not payload:
        raise ExtractionValidationError(
            {
                "status": "errors_found",
                "row_count": 0,
                "errors": ["GLM table JSON must be a non-empty list of rows."],
                "warnings": [],
            }
        )

    header = [_normalize_header(cell) for cell in _coerce_row(payload[0])]
    header_map = _header_map(header)
    missing = [name for name in REQUIRED_HEADERS if name not in header_map]
    if missing:
        raise ExtractionValidationError(
            {
                "status": "errors_found",
                "row_count": 0,
                "errors": [f"GLM table JSON is missing required headers: {', '.join(missing)}."],
                "warnings": [],
            }
        )

    rows = []
    warnings = []
    for visual_index, raw_row in enumerate(payload[1:], start=1):
        values = _coerce_row(raw_row)
        if not any(value.strip() for value in values):
            continue

        row = _row_dict(header, values)
        converted = {
            "source_index": visual_index,
            "location": row.get("LOCATION", ""),
            "characteristic": row.get("CHARACTERISTIC", ""),
            "raw_specification": row.get("SPECIFICATION", ""),
        }
        for header_name in OPTIONAL_HEADERS:
            converted[header_name.lower()] = row.get(header_name, "")
        if row.get("BUBBLE"):
            converted["bubble"] = row["BUBBLE"]
        if "[GD_REVIEW_REQUIRED]" in converted["raw_specification"]:
            converted.setdefault("warnings", []).append(
                "GD symbol classifier marked this row as review-required."
            )
        rows.append(converted)

    extraction = {
        "source_image": source_json or "",
        "source_json": source_json or "",
        "default_unit": default_unit,
        "rows": rows,
    }
    extraction, report = normalize_extraction(extraction)
    report["warnings"] = warnings + report.get("warnings", [])
    return extraction, report


def _header_map(header):
    return {name: index for index, name in enumerate(header) if name}


def _row_dict(header, values):
    width = max(len(header), len(values))
    padded_header = header + [""] * (width - len(header))
    padded_values = values + [""] * (width - len(values))
    row = {}
    for name, value in zip(padded_header, padded_values):
        if name:
            row[name] = value.strip()
    return row


def _coerce_row(row):
    if isinstance(row, list):
        return ["" if cell is None else str(cell) for cell in row]
    return ["" if row is None else str(row)]


def _normalize_header(value):
    text = str(value or "").strip().upper().replace("\n", " ")
    text = " ".join(text.split())
    return HEADER_ALIASES.get(text, text)


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
    parser = argparse.ArgumentParser(description="Convert final-table GLM JSON into pipeline extraction JSON.")
    parser.add_argument("input")
    parser.add_argument("--output")
    parser.add_argument("--default-unit", default="auto", choices=("auto", "inch", "metric", "mm", "in"))
    args = parser.parse_args(argv)

    try:
        _, report = convert_table_json_file(args.input, output_path=args.output, default_unit=args.default_unit)
    except ExtractionValidationError as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import json
import sys
from pathlib import Path

try:
    from extraction_schema import ExtractionValidationError, normalize_extraction
except ImportError:
    from .extraction_schema import ExtractionValidationError, normalize_extraction


REQUIRED_HEADERS = ("LOCATION", "CHARACTERISTIC", "SPECIFICATION")
COMBINED_SPECIFICATION_HEADER = "CHARACTERISTIC SPECIFICATION"
OPTIONAL_HEADERS = ("SPC", "ACTUAL", "COMMENTS", "PASS", "FAIL", "INITIAL")
HEADER_ALIASES = {
    "CHAR": "CHARACTERISTIC",
    "FEATURE": "CHARACTERISTIC",
    "SPEC": "SPECIFICATION",
    "REQUIREMENT": "SPECIFICATION",
    "CHARACTERISTIC/SPECIFICATION": COMBINED_SPECIFICATION_HEADER,
    "CHARACTERISTIC & SPECIFICATION": COMBINED_SPECIFICATION_HEADER,
    "CHARACTERISTICS SPECIFICATION": COMBINED_SPECIFICATION_HEADER,
    "CHARACTERISTIC SPECIFICATIONS": COMBINED_SPECIFICATION_HEADER,
}


def convert_table_json_file(input_path, output_path=None, default_unit="auto"):
    input_path = Path(input_path)
    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    image_assets = _load_image_assets(input_path)

    extraction, report = table_payload_to_extraction(
        payload,
        source_json=_display_path(input_path),
        default_unit=default_unit,
        image_assets=image_assets,
    )
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(extraction, handle, ensure_ascii=False, indent=2)
    return extraction, report


def table_payload_to_extraction(payload, source_json=None, default_unit="auto", image_assets=None):
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
    missing = _missing_required_headers(header_map)
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
    image_assets_by_row = _image_assets_by_row(image_assets)
    for visual_index, raw_row in enumerate(payload[1:], start=1):
        values = _coerce_row(raw_row)
        if not any(value.strip() for value in values):
            continue

        row = _row_dict(header, values)
        characteristic, raw_specification = _characteristic_and_specification(row)
        converted = {
            "source_index": visual_index,
            "location": row.get("LOCATION", ""),
            "characteristic": characteristic,
            "raw_specification": raw_specification,
        }
        for header_name in OPTIONAL_HEADERS:
            converted[header_name.lower()] = row.get(header_name, "")
        if row.get("BUBBLE"):
            converted["bubble"] = row["BUBBLE"]
        image_asset = image_assets_by_row.get(visual_index)
        if image_asset:
            converted["render_mode"] = "image"
            converted["specification_image"] = image_asset["path"]
            converted["specification_image_kind"] = image_asset.get("kind", "gd_frame")
            converted["specification_image_bbox"] = image_asset.get("bbox", [])
            converted.setdefault("warnings", []).append(
                "GD frame will be rendered from source image crop."
            )
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


def _load_image_assets(input_path):
    manifest_path = input_path.with_name(f"{input_path.stem}_image_assets.json")
    if not manifest_path.is_file():
        return []

    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    assets = []
    for asset in payload.get("assets", []):
        if not isinstance(asset, dict):
            continue
        resolved = dict(asset)
        path = _resolve_asset_path(manifest_path, asset)
        if path:
            resolved["path"] = str(path)
        assets.append(resolved)
    return assets


def _resolve_asset_path(manifest_path, asset):
    path = asset.get("path")
    if not path:
        return None
    value = Path(path)
    if value.is_absolute():
        return value
    directory = asset.get("directory")
    if directory:
        return manifest_path.parent / str(directory) / value
    return manifest_path.parent / value


def _image_assets_by_row(image_assets):
    rows = {}
    for asset in image_assets or []:
        if asset.get("kind") != "gd_frame":
            continue
        try:
            visual_index = int(asset.get("visual_index"))
        except (TypeError, ValueError):
            continue
        rows[visual_index] = asset
    return rows


def _missing_required_headers(header_map):
    missing = ["LOCATION"] if "LOCATION" not in header_map else []
    has_separate_specification = "CHARACTERISTIC" in header_map and "SPECIFICATION" in header_map
    has_combined_specification = COMBINED_SPECIFICATION_HEADER in header_map
    if not has_separate_specification and not has_combined_specification:
        missing.extend(["CHARACTERISTIC", "SPECIFICATION"])
    return missing


def _characteristic_and_specification(row):
    characteristic = row.get("CHARACTERISTIC", "")
    raw_specification = row.get("SPECIFICATION", "")
    combined = row.get(COMBINED_SPECIFICATION_HEADER, "")
    if combined:
        if not raw_specification:
            raw_specification = combined
        if not characteristic:
            characteristic = _infer_characteristic(combined)
    return characteristic, raw_specification


def _infer_characteristic(value):
    text = " ".join(str(value or "").replace("\n", " ").upper().split())
    if not text:
        return ""
    if "[GD_" in text or "GD&T" in text:
        return "GD&T"
    if any(term in text for term in ("SURFACE FINISH", "SURFACE ROUGHNESS", "ROUGHNESS", " RMS", " RA", " RZ")):
        return "SURFACE FINISH"
    if text.startswith("DIMENSION") or " DIAMETER" in text or "Ø" in text:
        return "DIMENSION"
    if text.startswith("TOLERANCE"):
        return "TOLERANCE"
    return text.split(maxsplit=1)[0]


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

import argparse
import copy
import json
import re
import sys


REQUIRED_ROW_FIELDS = ("source_index", "location", "characteristic", "raw_specification")
TEXT_ROW_FIELDS = ("location", "characteristic", "raw_specification")
OPTIONAL_TEXT_ROW_FIELDS = ("spc", "actual", "comments", "pass", "fail", "initial")
VALID_DEFAULT_UNITS = {"auto", "inch", "metric", "mm", "in"}
LOCATION_PATTERN = re.compile(r"^S?H?\d+-[A-Z]+\d*$")


class ExtractionValidationError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__("Extraction JSON validation failed.")


def normalize_extraction(payload, source_image=None, fill_optional=True):
    """Return a pipeline-ready extraction payload plus validation report."""
    data = copy.deepcopy(payload)
    errors = []
    warnings = []

    if not isinstance(data, dict):
        report = _report(errors=["Extraction payload must be a JSON object."], warnings=warnings, row_count=0)
        raise ExtractionValidationError(report)

    _normalize_source_image(data, source_image, warnings)
    _normalize_default_unit(data, warnings)

    rows = data.get("rows")
    if not isinstance(rows, list):
        errors.append("Field 'rows' must be a list.")
        rows = []
    if not rows:
        errors.append("Field 'rows' must contain at least one row.")

    normalized_rows = []
    for fallback_index, row in enumerate(rows, start=1):
        normalized = _normalize_row(row, fallback_index, fill_optional, errors, warnings)
        if normalized is not None:
            normalized_rows.append(normalized)

    data["rows"] = normalized_rows
    report = _report(errors=errors, warnings=warnings, row_count=len(normalized_rows))
    if errors:
        raise ExtractionValidationError(report)
    return data, report


def validate_extraction(payload, source_image=None):
    try:
        _, report = normalize_extraction(payload, source_image=source_image)
    except ExtractionValidationError as exc:
        return exc.report
    return report


def load_json_text(text):
    if isinstance(text, dict):
        return text
    cleaned = _strip_json_fence(str(text).strip())
    return json.loads(cleaned)


def _strip_json_fence(text):
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _normalize_source_image(data, source_image, warnings):
    existing = data.get("source_image")
    if not existing and source_image:
        data["source_image"] = source_image
        warnings.append("Missing source_image; filled from CLI input.")
    elif source_image and str(existing).replace("\\", "/") != str(source_image).replace("\\", "/"):
        warnings.append(f"source_image differs from CLI input: {existing!r} != {source_image!r}.")
    elif existing is not None:
        data["source_image"] = str(existing)


def _normalize_default_unit(data, warnings):
    if "default_unit" not in data:
        return
    value = str(data.get("default_unit") or "").strip().lower()
    if not value:
        data.pop("default_unit", None)
        return
    if value not in VALID_DEFAULT_UNITS:
        warnings.append(f"Ignoring unsupported default_unit: {data.get('default_unit')!r}.")
        data.pop("default_unit", None)
        return
    data["default_unit"] = "metric" if value == "mm" else "inch" if value == "in" else value


def _normalize_row(row, fallback_index, fill_optional, errors, warnings):
    label = f"rows[{fallback_index}]"
    if not isinstance(row, dict):
        errors.append(f"{label} must be an object.")
        return None

    normalized = copy.deepcopy(row)
    for field in REQUIRED_ROW_FIELDS:
        if field not in normalized:
            if field == "source_index":
                normalized[field] = fallback_index
                warnings.append(f"{label}.source_index missing; filled with visual row order.")
            else:
                errors.append(f"{label}.{field} is required.")

    normalized["source_index"] = _normalize_source_index(normalized.get("source_index"), fallback_index, label, errors)

    for field in TEXT_ROW_FIELDS:
        if field in normalized:
            normalized[field] = str(normalized[field]).strip()
            if normalized[field] == "":
                errors.append(f"{label}.{field} must not be blank.")

    if "warnings" in normalized:
        normalized["warnings"] = _normalize_warnings(normalized["warnings"], label, warnings)

    _normalize_location_ocr(normalized, label, warnings)

    if fill_optional:
        for field in OPTIONAL_TEXT_ROW_FIELDS:
            normalized[field] = str(normalized.get(field, "") or "")

    return normalized


def _normalize_source_index(value, fallback_index, label, errors):
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        errors.append(f"{label}.source_index must be an integer.")
        return fallback_index


def _normalize_warnings(value, label, warnings):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    warnings.append(f"{label}.warnings was not a list; coerced to string.")
    return [str(value)]


def _normalize_location_ocr(row, label, warnings):
    location = row.get("location")
    corrected = _correct_location_prefix_ocr(location)
    if corrected == location:
        return

    row.setdefault("raw_location", location)
    row["location"] = corrected
    row_warnings = row.get("warnings", [])
    if not isinstance(row_warnings, list):
        row_warnings = _normalize_warnings(row_warnings, label, warnings)
    message = f"Normalized LOCATION OCR confusion from {location!r} to {corrected!r}."
    if message not in row_warnings:
        row_warnings.append(message)
    row["warnings"] = row_warnings
    warnings.append(f"{label}.location normalized from {location!r} to {corrected!r}.")


def _correct_location_prefix_ocr(location):
    value = "" if location is None else str(location).strip()
    compact = value.replace(" ", "").upper()
    if _is_parseable_location(compact):
        return value

    candidates = []
    if re.match(r"^SI(?=\d+-)", compact):
        candidates.append("S" + compact[2:])
    if re.match(r"^SI(?=-)", compact):
        candidates.append("S1" + compact[2:])
    if re.match(r"^SHI(?=\d+-)", compact):
        candidates.append("SH" + compact[3:])
    if re.match(r"^SHI(?=-)", compact):
        candidates.append("SH1" + compact[3:])

    for candidate in candidates:
        if _is_parseable_location(candidate):
            return candidate
    return value


def _is_parseable_location(location):
    return bool(LOCATION_PATTERN.match(location or ""))


def _report(errors, warnings, row_count):
    return {
        "status": "success" if not errors else "errors_found",
        "row_count": row_count,
        "errors": errors,
        "warnings": warnings,
    }


def _configure_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def main(argv=None):
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Validate and normalize extraction JSON.")
    parser.add_argument("input")
    parser.add_argument("--source-image")
    parser.add_argument("--output", help="Optional path for normalized JSON.")
    args = parser.parse_args(argv)

    with open(args.input, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    try:
        normalized, report = normalize_extraction(payload, source_image=args.source_image)
    except ExtractionValidationError as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        return 1

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

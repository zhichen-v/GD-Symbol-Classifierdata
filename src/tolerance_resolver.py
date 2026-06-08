from parse_specification import PLUS_MINUS, decimal_places, inch_to_metric_text


def resolve_tolerance(parsed, tolerance_profile=None):
    if parsed.get("excel_tolerance") and parsed["excel_tolerance"] != "UNDETERMINED":
        return {
            "source": "explicit_specification",
            "final_tolerance": parsed["excel_tolerance"],
            "confidence": "high",
            "missing_evidence": [],
            "rule": parsed.get("tolerance_type") or "explicit",
        }

    if tolerance_profile:
        resolved = _resolve_from_profile(parsed, tolerance_profile)
        if resolved:
            return resolved

    return {
        "source": "missing_general_tolerance_source",
        "final_tolerance": "-",
        "confidence": "low",
        "missing_evidence": [
            "No title-block, general-note, company, customer, or referenced tolerance table was supplied."
        ],
        "rule": "ASME Y14.5-2009 does not define a universal numeric default tolerance table.",
    }


def _resolve_from_profile(parsed, profile):
    if parsed.get("kind") != "linear" or not parsed.get("nominal"):
        return None

    unit_profile = _profile_for_unit(parsed, profile)
    if not unit_profile:
        return None

    table = (unit_profile.get("tables") or {}).get("linear_decimal") or {}
    nominal_digits = decimal_places(parsed["nominal"])
    row = table.get(str(nominal_digits))
    if not row:
        return None

    if row.get("display"):
        return {
            "source": _profile_value(unit_profile, row, "source", "provided_tolerance_profile"),
            "final_tolerance": row["display"],
            "confidence": _profile_value(unit_profile, row, "confidence", "medium"),
            "missing_evidence": [],
            "rule": f"linear_decimal[{nominal_digits}]",
        }

    plus = str(row.get("plus", ""))
    minus = str(row.get("minus", plus))
    if plus == minus:
        tolerance = f"{PLUS_MINUS}{plus}"
        if parsed.get("unit") == "inch":
            tolerance = f"{tolerance}({_inch_tolerance_metric_text(plus)})"
    else:
        tolerance = f"+{plus}/-{minus}"
        if parsed.get("unit") == "inch":
            tolerance = (
                f"+{plus}({_inch_tolerance_metric_text(plus)})"
                f"/-{minus}({_inch_tolerance_metric_text(minus)})"
            )

    return {
        "source": _profile_value(unit_profile, row, "source", "provided_tolerance_profile"),
        "final_tolerance": tolerance,
        "confidence": _profile_value(unit_profile, row, "confidence", "medium"),
        "missing_evidence": [],
        "rule": f"linear_decimal[{nominal_digits}]",
    }


def _profile_for_unit(parsed, profile):
    parsed_unit = _normalize_unit_alias(parsed.get("unit"))
    unit_tables = profile.get("unit_tables") or profile.get("units") or {}

    if isinstance(unit_tables, dict) and unit_tables:
        for unit_name, unit_profile in unit_tables.items():
            if _normalize_unit_alias(unit_name) != parsed_unit:
                continue
            if not isinstance(unit_profile, dict):
                continue
            merged = dict(profile)
            merged.update(unit_profile)
            merged["unit"] = _normalize_unit_alias(unit_name)
            return merged
        return None

    if _profile_unit_matches(parsed, profile):
        return profile
    return None


def _profile_value(profile, row, key, default):
    return row.get(key) or profile.get(key) or default


def _inch_tolerance_metric_text(number_text):
    places = 3 if decimal_places(number_text) >= 3 else 2
    return inch_to_metric_text(number_text, places=places)


def _profile_unit_matches(parsed, profile):
    profile_unit = _normalize_unit_alias(profile.get("unit"))
    parsed_unit = _normalize_unit_alias(parsed.get("unit"))
    if not profile_unit or not parsed_unit:
        return True
    return profile_unit == parsed_unit


def _normalize_unit_alias(unit):
    value = (unit or "").strip().lower()
    aliases = {
        "in": "inch",
        "imperial": "inch",
        "mm": "metric",
        "millimeter": "metric",
        "millimetre": "metric",
    }
    return aliases.get(value, value)

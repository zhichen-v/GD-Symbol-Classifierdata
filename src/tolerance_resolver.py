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
    if not _profile_unit_matches(parsed, profile):
        return None

    table = (profile.get("tables") or {}).get("linear_decimal") or {}
    nominal_digits = decimal_places(parsed["nominal"])
    row = table.get(str(nominal_digits))
    if not row:
        return None

    if row.get("display"):
        return {
            "source": profile.get("source", "provided_tolerance_profile"),
            "final_tolerance": row["display"],
            "confidence": profile.get("confidence", "medium"),
            "missing_evidence": [],
            "rule": f"linear_decimal[{nominal_digits}]",
        }

    plus = str(row.get("plus", ""))
    minus = str(row.get("minus", plus))
    if plus == minus:
        tolerance = f"{PLUS_MINUS}{plus}"
        if parsed.get("unit") == "inch":
            tolerance = f"{tolerance}({inch_to_metric_text(plus)})"
    else:
        tolerance = f"+{plus}/-{minus}"
        if parsed.get("unit") == "inch":
            tolerance = f"+{plus}({inch_to_metric_text(plus)})/-{minus}({inch_to_metric_text(minus)})"

    return {
        "source": profile.get("source", "provided_tolerance_profile"),
        "final_tolerance": tolerance,
        "confidence": "medium",
        "missing_evidence": [],
        "rule": f"linear_decimal[{nominal_digits}]",
    }


def _profile_unit_matches(parsed, profile):
    profile_unit = (profile.get("unit") or "").strip().lower()
    parsed_unit = (parsed.get("unit") or "").strip().lower()
    if not profile_unit or not parsed_unit:
        return True
    aliases = {
        "in": "inch",
        "imperial": "inch",
        "mm": "metric",
        "millimeter": "metric",
        "millimetre": "metric",
    }
    return aliases.get(profile_unit, profile_unit) == aliases.get(parsed_unit, parsed_unit)

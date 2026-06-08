import re
from decimal import Decimal, ROUND_HALF_UP


INCH_TO_MM = Decimal("25.4")
MICROINCH_TO_MICROMETER = Decimal("0.0254")

PLUS_MINUS = "\u00b1"
DIAMETER = "\u00d8"
FLATNESS = "\u23e5"
POSITION = "\u2316"
PARALLELISM = "\u2225"
PROFILE = "\u2312"
PROFILE_SURFACE = "\u2313"
ANGULARITY = "\u2220"
PERPENDICULARITY = "\u27c2"
CIRCULARITY = "\u25cb"
CONCENTRICITY = "\u25ce"
CYLINDRICITY = "\u232d"
SYMMETRY = "\u232f"
CIRCULAR_RUNOUT = "\u2197"
TOTAL_RUNOUT = "\u2330"
MICRO = "\u00b5"

NUMBER = r"(?:\d+\.\d+|\.\d+|\d+)"
SIGNED_NUMBER = rf"[+-]?{NUMBER}"
DEGREE = "\u00b0"
GD_REVIEW_TAG = "[GD_REVIEW_REQUIRED]"
GD_TAG_SYMBOLS = {
    "GD_ANGULARITY": ANGULARITY,
    "GD_CIRCULARITY": CIRCULARITY,
    "GD_CIRCULAR_RUNOUT": CIRCULAR_RUNOUT,
    "GD_CONCENTRICITY": CONCENTRICITY,
    "GD_CYLINDRICITY": CYLINDRICITY,
    "GD_FLATNESS": FLATNESS,
    "GD_PARALLELISM": PARALLELISM,
    "GD_PERPENDICULARITY": PERPENDICULARITY,
    "GD_POSITION": POSITION,
    "GD_PROFILE_LINE": PROFILE,
    "GD_PROFILE_SURFACE": PROFILE_SURFACE,
    "GD_SYMMETRY": SYMMETRY,
    "GD_TOTAL_RUNOUT": TOTAL_RUNOUT,
}
GD_TERM_SYMBOLS = {
    "ANGULARITY": ANGULARITY,
    "CIRCULARITY": CIRCULARITY,
    "CIRCULAR RUNOUT": CIRCULAR_RUNOUT,
    "CONCENTRICITY": CONCENTRICITY,
    "CYLINDRICITY": CYLINDRICITY,
    "FLATNESS": FLATNESS,
    "PARALLELISM": PARALLELISM,
    "PERPENDICULARITY": PERPENDICULARITY,
    "POSITION": POSITION,
    "TRUE POSITION": POSITION,
    "PROFILE LINE": PROFILE,
    "PROFILE SURFACE": PROFILE_SURFACE,
    "SYMMETRY": SYMMETRY,
    "TOTAL RUNOUT": TOTAL_RUNOUT,
}
GD_SYMBOLS = tuple(dict.fromkeys(GD_TAG_SYMBOLS.values()))


def normalize_spec_text(text):
    value = "" if text is None else str(text).strip()
    value = _normalize_latex_text(value)
    replacements = {
        "\u7c23": PLUS_MINUS,
        "¡Ó": PLUS_MINUS,
        "+/-": PLUS_MINUS,
        "±": PLUS_MINUS,
        "⌀": DIAMETER,
        "ø": DIAMETER,
        "∅": DIAMETER,
        "Ø": DIAMETER,
        "▱": FLATNESS,
        "⌖": POSITION,
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = _replace_gd_tags(value)
    value = value.replace("[M]", "(M)")
    value = re.sub(r"(?i)\bE[O0]LSP\b", "EQLSP", value)
    value = re.sub(rf"{re.escape(DIAMETER)}\s*,\s*(?=\d)", DIAMETER, value)
    value = _normalize_unilateral_text(value)
    value = _compact_spaced_digits(value)
    value = _normalize_unilateral_text(value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*/\s*", "/", value)
    value = _normalize_gdt_separators(value)
    value = re.sub(rf"{re.escape(DIAMETER)}\s*\.(?=\d+\.)", DIAMETER, value)
    return value.strip()


def _normalize_latex_text(value):
    value = _normalize_numeric_commas(value)
    value = re.sub(r"(?i)(?:\\|/)pm", PLUS_MINUS, value)
    value = re.sub(r"(?i)(?:\\|/)mu", MICRO, value)
    value = re.sub(r"(?i)(?:\\|/)phi\b", DIAMETER, value)
    value = re.sub(r"(?i)(?:\\|/)circ", DEGREE, value)
    value = re.sub(r"\^\s*\{\s*" + re.escape(DEGREE) + r"\s*\}", DEGREE, value)
    value = _normalize_latex_tolerance_scripts(value)
    value = re.sub(r"[\^_]\s*\{\s*([^{}]+?)\s*\}", r" \1", value)
    value = value.replace("$", " ")
    value = value.replace("{", "").replace("}", "")
    value = value.replace("\\", "")
    value = re.sub(r"(?i)\bphi\b(?=\s*\.?\d)", DIAMETER, value)
    value = _normalize_numeric_commas(value)
    return value


def _normalize_latex_tolerance_scripts(value):
    diameter_nominal_pattern = re.compile(
        rf"{re.escape(DIAMETER)}\s*_\s*\{{(?P<low>{SIGNED_NUMBER})\}}\s*"
        rf"\^\s*\{{(?P<nom>{NUMBER})(?P<upper>[+-]{NUMBER})\}}"
    )
    value = diameter_nominal_pattern.sub(
        lambda match: f"{DIAMETER}{match.group('nom')} {match.group('upper')}/{match.group('low')}",
        value,
    )

    nominal_pattern = re.compile(
        rf"(?P<nom>{re.escape(DIAMETER)}?\s*{NUMBER})\s*_\s*\{{(?P<low>{SIGNED_NUMBER})\}}\s*"
        rf"\^\s*\{{(?P<upper>{SIGNED_NUMBER})\}}"
    )
    value = nominal_pattern.sub(
        lambda match: f"{match.group('nom')} {match.group('upper')}/{match.group('low')}",
        value,
    )
    return value


def _normalize_numeric_commas(value):
    value = re.sub(r"([+-]),(?=\d)", r"\1.", value)
    return re.sub(r"(?<=\d),(?=\d)", ".", value)


def _replace_gd_tags(value):
    def replace(match):
        tag = match.group(1).upper()
        if tag == "GD_REVIEW_REQUIRED":
            return GD_REVIEW_TAG
        return GD_TAG_SYMBOLS.get(tag, match.group(0))

    return re.sub(r"\[(GD_[A-Z_]+)\]", replace, value, flags=re.IGNORECASE)


def _compact_spaced_digits(value):
    value = re.sub(
        r"(?<![\d.])\d(?:\s+\d)+(?=\s*\.)",
        lambda match: match.group(0).replace(" ", ""),
        value,
    )
    value = re.sub(
        rf"{re.escape(DIAMETER)}\s+(?P<num>\d(?:\s+\d)+)(?=\D|$)",
        lambda match: f"{DIAMETER}{match.group('num').replace(' ', '')}",
        value,
    )
    value = re.sub(r"(?<!\d)\.\s+(?=\d)", ".", value)
    value = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", value)
    previous = None
    while previous != value:
        previous = value
        value = re.sub(
            r"(?P<num>(?:\d+\.\d+|\.\d+))\s+(?P<digit>\d)(?!\s*/)",
            r"\g<num>\g<digit>",
            value,
        )
    value = re.sub(r"([+-])\s+(?=\.?\d)", r"\1", value)
    value = re.sub(rf"{re.escape(DIAMETER)}\s+(?=\.?\d)", DIAMETER, value)
    return value


def _normalize_unilateral_text(value):
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(
        rf"(?P<nom>{NUMBER})\s+(?P<plus>\+{NUMBER})\s+(?P<low>0)(?=\s*$)",
        r"\g<nom> \g<plus>/\g<low>",
        value,
    )
    value = re.sub(
        rf"(?P<nom>{NUMBER})\s+(?P<plus>0)\s*(?P<minus>-{NUMBER})(?=\s*$)",
        r"\g<nom> \g<plus>/\g<minus>",
        value,
    )
    value = re.sub(r"(?<!\d)(?P<nom>\d{2})0/(?=-0\.)", r"\g<nom> 0/", value)
    value = re.sub(
        rf"^(?P<prefix>.*?)(?P<nom>{NUMBER})\s*/\s*(?P<minus>-{NUMBER})$",
        _add_zero_upper_tolerance,
        value,
    )
    return value


def _add_zero_upper_tolerance(match):
    prefix = match.group("prefix")
    if re.search(NUMBER, prefix):
        return match.group(0)
    return f"{prefix}{match.group('nom')} 0/{match.group('minus')}"


def _normalize_gdt_separators(value):
    if not _contains_gdt_symbol(value):
        return value
    value = re.sub(r"\s*/\s*/\s*", " | ", value)
    value = re.sub(r"\s*\|\s*", " | ", value)
    return re.sub(r"(?:\s*\|\s*)+$", "", value)


def _contains_gdt_symbol(value):
    return GD_REVIEW_TAG in value or any(symbol in value for symbol in GD_SYMBOLS)


def parse_specification(raw_specification, characteristic="", default_unit="auto", infer_unit=True):
    normalized = normalize_spec_text(raw_specification)
    char = (characteristic or "").upper()
    unit = resolve_unit(normalized, char, default_unit=default_unit, infer_unit=infer_unit)

    if _is_gdt(char, normalized):
        parsed = _parse_gdt(normalized, char, unit)
    elif _is_surface_roughness(char, normalized):
        parsed = _parse_surface_roughness(normalized, char)
    elif _is_thread(normalized, char):
        parsed = _base_result(normalized, "thread", unit)
        parsed.update(
            {
                "excel_specification": normalized,
                "excel_tolerance": "N/A",
                "tolerance_type": "thread_class",
                "requires_default_tolerance": False,
            }
        )
    else:
        parsed = _parse_linear(normalized, char, unit)

    parsed["characteristic"] = characteristic
    return parsed


def resolve_unit(text, characteristic="", default_unit="auto", infer_unit=True):
    fallback = _normalize_unit(default_unit) or "inch"
    if not infer_unit:
        return fallback
    inferred = infer_unit_from_specification(text, characteristic)
    return inferred or fallback


def infer_unit_from_specification(raw_specification, characteristic=""):
    text = normalize_spec_text(raw_specification)
    char = (characteristic or "").upper()
    if _is_surface_roughness(char, text) or _is_thread(text, char):
        return None

    tolerance_tokens = _tolerance_tokens(text, char)
    for token in tolerance_tokens:
        unit = _unit_from_number_token(token)
        if unit:
            return unit

    nominal = _nominal_token(text)
    if nominal and nominal.startswith("."):
        return "inch"
    return None


def _normalize_unit(unit):
    value = (unit or "").strip().lower()
    if value in {"inch", "in", "imperial"}:
        return "inch"
    if value in {"metric", "mm", "millimeter", "millimetre"}:
        return "metric"
    if value in {"degree", "degrees", "angle", "angular"}:
        return "degree"
    return None


def _tolerance_tokens(text, characteristic):
    bilateral = re.search(
        rf"(?P<nom>{NUMBER})(?:{DEGREE})?\s*{re.escape(PLUS_MINUS)}\s*(?P<tol>{NUMBER})(?:{DEGREE})?",
        text,
    )
    if bilateral:
        if DEGREE in bilateral.group(0):
            return []
        return [bilateral.group("tol")]

    unilateral = _unilateral_match(text)
    if unilateral:
        if DEGREE in unilateral.group(0):
            return []
        return [_unilateral_plus(unilateral), unilateral.group("minus")]

    if _is_gdt(characteristic, text):
        tokens = _gdt_tokens(text, _gdt_symbol(characteristic, text))
        for token in tokens:
            match = re.search(rf"{DIAMETER}?\s*(?P<num>{NUMBER})", token)
            if match:
                return [match.group("num")]

    return []


def _unit_from_number_token(token):
    value = token.strip().lstrip("+-")
    if value.startswith("."):
        return "inch"
    if re.match(r"^0\.\d+", value):
        return "metric"
    return None


def _unilateral_match(text, anchored=False):
    prefix = r"^(?P<prefix>.*?)" if anchored else ""
    pattern = (
        rf"{prefix}(?P<nom>{NUMBER})(?:{DEGREE})?"
        rf"(?:\s+(?P<plus_spaced>\+?{NUMBER})|\s*(?P<plus_signed>\+{NUMBER}))"
        rf"(?:\s*/\s*|\s+)(?P<minus>[+-]?{NUMBER})(?:{DEGREE})?$"
    )
    return re.search(pattern, text)


def _unilateral_plus(match):
    return match.group("plus_spaced") or match.group("plus_signed")


def _single_sided_tolerance_match(text):
    return re.search(
        rf"^(?P<prefix>.*?)(?P<nom>{NUMBER})(?P<degree>{DEGREE})?\s*(?P<tol>[+-]{NUMBER})(?:{DEGREE})?$",
        text,
    )


def _unsigned_bilateral_tolerance_match(text):
    patterns = (
        rf"^(?P<prefix>.*?)(?P<nom>{NUMBER})(?P<degree>{DEGREE})?\s*(?::|\s+)\s*(?P<tol>{NUMBER})(?:{DEGREE})?$",
        rf"^(?P<prefix>.*?)(?P<nom>(?:\d+\.\d+|\.\d+))(?P<degree>{DEGREE})?(?P<tol>\.\d+)(?:{DEGREE})?$",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match and not _prefix_contains_numeric_value(match.group("prefix")):
            return match
    return None


def _prefix_contains_numeric_value(prefix):
    value = re.sub(r"\b\d+\s*X\b", "", prefix or "", flags=re.IGNORECASE)
    value = value.replace(DIAMETER, " ")
    return bool(re.search(NUMBER, value))


def _nominal_token(text):
    match = re.search(rf"^(?P<prefix>.*?)(?P<nom>{NUMBER})(?:{DEGREE})?$", text)
    return match.group("nom") if match else None


def _base_result(text, kind, unit):
    return {
        "source_text": text,
        "normalized_text": text,
        "kind": kind,
        "unit": unit,
        "nominal": "",
        "tolerance": "",
        "tolerance_type": "",
        "metric_nominal": "",
        "metric_tolerance": "",
        "excel_specification": "",
        "excel_tolerance": "",
        "requires_default_tolerance": False,
        "warnings": [],
    }


def _is_gdt(characteristic, text):
    if characteristic.strip() in {"GD", "GD&T"}:
        return True
    terms = tuple(GD_TERM_SYMBOLS)
    return any(term in characteristic for term in terms) or _contains_gdt_symbol(text)


def _is_surface_roughness(characteristic, text):
    return any(
        term in f"{characteristic} {text}".upper()
        for term in ("SURFACE FINISH", "SURFACE ROUGHNESS", "ROUGHNESS", " RMS", " RA", " RZ")
    )


def _is_thread(text, characteristic):
    combined = f"{characteristic} {text}".upper()
    return bool(
        re.search(r"\b(?:UNC|UNF|UNEF|NPT|BSP|THREAD|TAP|TAPPED)\b", combined)
        or re.search(r"\bM\d+(?:\s*[Xx]\s*\d+(?:\.\d+)?)?\b", combined)
    )


def _parse_gdt(text, characteristic, unit):
    result = _base_result(text, "gdt", unit)
    symbol = _gdt_symbol(characteristic, text)
    tokens = _gdt_tokens(text, symbol)

    tol_index = None
    tol_text = ""
    for index, token in enumerate(tokens):
        match = re.search(rf"(?P<diam>{DIAMETER})?\s*(?P<num>{NUMBER})", token)
        if match:
            tol_index = index
            prefix = DIAMETER if match.group("diam") else ""
            tol_text = f"{prefix}{match.group('num')}"
            break

    if not tol_text:
        result["excel_specification"] = symbol
        result["excel_tolerance"] = "-"
        result["requires_default_tolerance"] = False
        result["warnings"].append("GD&T callout did not contain a readable tolerance value.")
        return result

    number_match = re.search(NUMBER, tol_text)
    metric = metric_value_text(number_match.group(0), unit, places=_metric_places(number_match.group(0), is_tolerance=True))
    spec_tolerance = _format_gdt_tolerance(tol_text)
    datums = tokens[tol_index + 1 :] if tol_index is not None else []
    spec_parts = [symbol, spec_tolerance] + datums

    result.update(
        {
            "nominal": "",
            "tolerance": tol_text,
            "tolerance_type": "gdt",
            "metric_tolerance": metric,
            "excel_specification": " ".join(spec_parts),
            "excel_tolerance": limit_tolerance_text(number_match.group(0), unit),
            "requires_default_tolerance": False,
        }
    )
    return result


def _gdt_symbol(characteristic, text):
    if text.startswith(GD_REVIEW_TAG):
        return GD_REVIEW_TAG
    for symbol in GD_SYMBOLS:
        if symbol in text:
            return symbol
    normalized_characteristic = " ".join(characteristic.replace("_", " ").upper().split())
    for term, symbol in GD_TERM_SYMBOLS.items():
        if term in normalized_characteristic:
            return symbol
    return text[:1] if text else ""


def _gdt_tokens(text, symbol):
    value = text.strip()
    if symbol and value.startswith(symbol):
        value = value[len(symbol) :].strip()

    if "|" in value:
        tokens = [part.strip() for part in re.split(r"\|", value) if part.strip()]
    else:
        tokens = [part.strip() for part in value.split() if part.strip()]
    tokens = _expand_gdt_tokens(tokens)
    return _merge_gdt_number_tokens(tokens)


def _expand_gdt_tokens(tokens):
    expanded = []
    for token in tokens:
        if " " in token and re.search(NUMBER, token):
            expanded.extend(part for part in token.split() if part)
        else:
            expanded.append(token)
    return expanded


def _merge_gdt_number_tokens(tokens):
    merged = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == DIAMETER and index + 1 < len(tokens) and re.search(NUMBER, tokens[index + 1]):
            merged.append(f"{DIAMETER}{tokens[index + 1]}")
            index += 2
            continue
        merged.append(token)
        index += 1
    return merged


def _parse_surface_roughness(text, characteristic):
    result = _base_result(text, "surface_roughness", "microinch")
    range_match = re.search(rf"(?P<low>\d+(?:\.\d+)?)\s*[-~]\s*(?P<high>\d+(?:\.\d+)?)", text)
    single_match = re.search(rf"(?P<value>\d+(?:\.\d+)?)\s*(?:RMS|RA|RZ)?", text, re.IGNORECASE)

    if range_match:
        low = range_match.group("low")
        high = range_match.group("high")
        ra_low = microinch_to_ra_text(low)
        ra_high = microinch_to_ra_text(high)
        roughness = f"{low}-{high} RMS(Ra {ra_low}-{ra_high})"
        result.update(
            {
                "nominal": f"{low}-{high}",
                "tolerance": roughness,
                "tolerance_type": "roughness_range",
                "metric_tolerance": f"Ra {ra_low}-{ra_high}",
                "excel_specification": roughness,
                "excel_tolerance": limit_tolerance_text(high),
                "requires_default_tolerance": False,
            }
        )
        return result

    if single_match and (_has_roughness_token(text) or _is_simple_roughness_value(text)):
        value = single_match.group("value")
        ra = microinch_to_ra_text(value)
        roughness = f"\u2264{value} RMS(Ra {ra})"
        result.update(
            {
                "nominal": value,
                "tolerance": roughness,
                "tolerance_type": "roughness_limit",
                "metric_tolerance": f"Ra {ra}",
                "excel_specification": text,
                "excel_tolerance": limit_tolerance_text(value),
                "requires_default_tolerance": False,
            }
        )
        return result

    result.update(
        {
            "excel_specification": text,
            "excel_tolerance": "-",
            "requires_default_tolerance": False,
        }
    )
    return result


def _has_roughness_token(text):
    return any(token in text.upper() for token in ("RMS", "RA", "RZ"))


def _is_simple_roughness_value(text):
    return bool(
        re.fullmatch(
            rf"\s*(?:\u2264|<=)?\s*{NUMBER}\s*(?:\u00b5\s*m|um|micron|microinch|uin)?\s*",
            text,
            re.IGNORECASE,
        )
    )


def _parse_linear(text, characteristic, unit):
    result = _base_result(text, "linear", unit)
    core_text, trailing_note = _split_linear_trailing_note(text)
    unilateral = _unilateral_match(core_text, anchored=True)
    if unilateral:
        prefix = _clean_prefix(unilateral.group("prefix"))
        nominal = unilateral.group("nom")
        plus = _signed(_unilateral_plus(unilateral), "+")
        minus = _signed(unilateral.group("minus"), "-")
        excel_tolerance = _format_unilateral_tolerance(plus, minus, unit)
        result.update(
            {
                "nominal": f"{prefix}{nominal}",
                "tolerance": f"{plus}/{minus}",
                "tolerance_type": "unilateral",
                "metric_nominal": metric_value_text(nominal, unit, places=_metric_places(nominal)),
                "metric_tolerance": f"{metric_value_text(plus[1:], unit, places=_metric_places(plus, is_tolerance=True))}/{metric_value_text(minus[1:], unit, places=_metric_places(minus, is_tolerance=True))}",
                "excel_specification": _append_trailing_note(
                    _format_nominal(prefix, nominal, unit),
                    trailing_note,
                ),
                "excel_tolerance": excel_tolerance,
                "requires_default_tolerance": False,
            }
        )
        return result

    single_sided = _single_sided_tolerance_match(core_text)
    if single_sided:
        prefix = _clean_prefix(single_sided.group("prefix"))
        is_angle = bool(single_sided.group("degree"))
        value_unit = "degree" if is_angle else unit
        nominal = single_sided.group("nom")
        signed_tolerance = single_sided.group("tol")
        if signed_tolerance.startswith("+"):
            plus = signed_tolerance
            minus = "-0"
        else:
            plus = "+0"
            minus = signed_tolerance
        excel_tolerance = _format_unilateral_tolerance(plus, minus, value_unit)
        result.update(
            {
                "nominal": f"{prefix}{nominal}",
                "tolerance": f"{plus}/{minus}",
                "tolerance_type": "unilateral",
                "unit": value_unit,
                "metric_nominal": metric_value_text(nominal, value_unit, places=_metric_places(nominal)),
                "metric_tolerance": f"{metric_value_text(plus[1:], value_unit, places=_metric_places(plus, is_tolerance=True))}/{metric_value_text(minus[1:], value_unit, places=_metric_places(minus, is_tolerance=True))}",
                "excel_specification": _append_trailing_note(
                    _format_nominal(prefix, nominal, value_unit, suffix=DEGREE if is_angle else ""),
                    trailing_note,
                ),
                "excel_tolerance": excel_tolerance,
                "requires_default_tolerance": False,
            }
        )
        return result

    bilateral = re.search(
        rf"^(?P<prefix>.*?)(?P<nom>{NUMBER})(?P<degree>{DEGREE})?\s*{re.escape(PLUS_MINUS)}\s*(?P<tol>{NUMBER})(?:{DEGREE})?$",
        core_text,
    )
    if bilateral:
        prefix = _clean_prefix(bilateral.group("prefix"))
        is_angle = bool(bilateral.group("degree"))
        value_unit = "degree" if is_angle else unit
        nominal = bilateral.group("nom")
        tolerance = bilateral.group("tol")
        tolerance_suffix = DEGREE if is_angle else ""
        excel_tolerance = f"{PLUS_MINUS}{tolerance}{tolerance_suffix}"
        if value_unit == "inch":
            excel_tolerance = f"{excel_tolerance}({inch_to_metric_text(tolerance, places=_metric_places(tolerance, is_tolerance=True))})"
        result.update(
            {
                "nominal": f"{prefix}{nominal}",
                "tolerance": f"{PLUS_MINUS}{tolerance}",
                "tolerance_type": "bilateral",
                "unit": value_unit,
                "metric_nominal": metric_value_text(nominal, value_unit, places=_metric_places(nominal)),
                "metric_tolerance": metric_value_text(tolerance, value_unit, places=_metric_places(tolerance, is_tolerance=True)),
                "excel_specification": _append_trailing_note(
                    _format_nominal(prefix, nominal, value_unit, suffix=DEGREE if is_angle else ""),
                    trailing_note,
                ),
                "excel_tolerance": excel_tolerance,
                "requires_default_tolerance": False,
            }
        )
        return result

    unsigned_bilateral = _unsigned_bilateral_tolerance_match(core_text)
    if unsigned_bilateral:
        prefix = _clean_prefix(unsigned_bilateral.group("prefix"))
        is_angle = bool(unsigned_bilateral.group("degree"))
        value_unit = "degree" if is_angle else unit
        nominal = unsigned_bilateral.group("nom")
        tolerance = unsigned_bilateral.group("tol")
        tolerance_suffix = DEGREE if is_angle else ""
        excel_tolerance = f"{PLUS_MINUS}{tolerance}{tolerance_suffix}"
        if value_unit == "inch":
            excel_tolerance = f"{excel_tolerance}({inch_to_metric_text(tolerance, places=_metric_places(tolerance, is_tolerance=True))})"
        result.update(
            {
                "nominal": f"{prefix}{nominal}",
                "tolerance": f"{PLUS_MINUS}{tolerance}",
                "tolerance_type": "bilateral_inferred",
                "unit": value_unit,
                "metric_nominal": metric_value_text(nominal, value_unit, places=_metric_places(nominal)),
                "metric_tolerance": metric_value_text(tolerance, value_unit, places=_metric_places(tolerance, is_tolerance=True)),
                "excel_specification": _append_trailing_note(
                    _format_nominal(prefix, nominal, value_unit, suffix=DEGREE if is_angle else ""),
                    trailing_note,
                ),
                "excel_tolerance": excel_tolerance,
                "requires_default_tolerance": False,
            }
        )
        result["warnings"].append("Inferred bilateral tolerance from adjacent nominal/tolerance OCR text.")
        return result

    nominal_match = re.search(rf"^(?P<prefix>.*?)(?P<nom>{NUMBER})$", core_text)
    if nominal_match and len(re.findall(NUMBER, core_text)) == 1:
        prefix = _clean_prefix(nominal_match.group("prefix"))
        nominal = nominal_match.group("nom")
        result.update(
            {
                "nominal": f"{prefix}{nominal}",
                "metric_nominal": metric_value_text(nominal, unit, places=_metric_places(nominal)),
                "excel_specification": _append_trailing_note(
                    _format_nominal(prefix, nominal, unit),
                    trailing_note,
                ),
                "excel_tolerance": "UNDETERMINED",
                "requires_default_tolerance": True,
            }
        )
        result["warnings"].append("No explicit tolerance found; default tolerance source is required.")
        return result

    result.update(
        {
            "kind": "note",
            "excel_specification": text,
            "excel_tolerance": "UNDETERMINED",
            "requires_default_tolerance": True,
        }
    )
    result["warnings"].append("Specification did not match a supported deterministic parser pattern.")
    return result


def _split_linear_trailing_note(text):
    match = re.search(r"(?i)^(?P<core>.+?)\s+(?P<note>THRU|EQLSP|EQ\s+SP)$", text)
    if not match:
        return text, ""
    note = re.sub(r"\s+", " ", match.group("note").upper())
    return match.group("core").strip(), note


def _append_trailing_note(value, trailing_note):
    return f"{value} {trailing_note}".strip()


def _clean_prefix(prefix):
    value = (prefix or "").strip()
    if DIAMETER in value:
        return value
    return value


def _signed(text, default_sign):
    value = text.strip()
    if value.startswith(("+", "-")):
        return value
    return f"{default_sign}{value}"


def _format_nominal(prefix, number_text, unit, suffix=""):
    nominal = f"{prefix}{number_text}{suffix}".strip()
    if unit != "inch":
        return nominal
    return f"{nominal}({inch_to_metric_text(number_text, places=_metric_places(number_text))})"


def _format_gdt_tolerance(text):
    match = re.search(NUMBER, text)
    if not match:
        return text
    number_text = match.group(0)
    display = number_text if not number_text.startswith(".") else f"0{number_text}"
    return text.replace(number_text, display, 1)


def limit_tolerance_text(number_text, unit=""):
    display = number_text if not number_text.startswith(".") else f"0{number_text}"
    tolerance = f"\u2264{display}"
    if unit == "inch":
        tolerance = f"{tolerance}({inch_to_metric_text(number_text, places=_metric_places(number_text, is_tolerance=True))})"
    return tolerance


def first_limit_tolerance_text(text, unit=""):
    number_text = first_limit_number_text(text)
    if not number_text:
        return ""
    return limit_tolerance_text(number_text, unit=unit)


def first_limit_number_text(text):
    value = "" if text is None else str(text)
    for match in re.finditer(NUMBER, value):
        if _is_quantity_prefix(value, match.end()):
            continue
        return match.group(0)
    return ""


def _is_quantity_prefix(value, match_end):
    remainder = value[match_end:].lstrip()
    return remainder[:1].upper() == "X"


def _format_unilateral_tolerance(plus, minus, unit):
    return "/".join(
        (
            _format_signed_tolerance(plus, unit),
            _format_signed_tolerance(minus, unit),
        )
    )


def _is_zero_text(text):
    return to_decimal(text[1:]) == 0


def _format_signed_tolerance(text, unit):
    sign = text[0]
    number_text = text[1:]
    if _is_zero_text(text):
        return f"{sign}0"
    if unit != "inch":
        return text
    return f"{sign}{number_text}({inch_to_metric_text(number_text, places=_metric_places(text, is_tolerance=True))})"


def metric_value_text(number_text, unit, places=3):
    if unit == "inch":
        return inch_to_metric_text(number_text, places=places)
    if unit == "metric":
        return number_text.strip().lstrip("+")
    return ""


def _metric_places(number_text, is_tolerance=False):
    places = decimal_places(number_text)
    if is_tolerance:
        return 3 if places >= 3 else 2
    return 3


def decimal_places(number_text):
    cleaned = number_text.strip().lstrip("+-")
    if "." not in cleaned:
        return 0
    return len(cleaned.split(".", 1)[1])


def inch_to_metric_text(number_text, places=3):
    value = to_decimal(number_text) * INCH_TO_MM
    return format_decimal(value, places)


def microinch_to_ra_text(number_text):
    value = to_decimal(number_text) * MICROINCH_TO_MICROMETER
    return format_decimal(value, 1)


def to_decimal(number_text):
    cleaned = number_text.strip().lstrip("+")
    if cleaned.startswith("."):
        cleaned = "0" + cleaned
    if cleaned.startswith("-."):
        cleaned = "-0" + cleaned[1:]
    return Decimal(cleaned)


def format_decimal(value, places):
    quant = Decimal("1").scaleb(-places)
    return str(value.quantize(quant, rounding=ROUND_HALF_UP))

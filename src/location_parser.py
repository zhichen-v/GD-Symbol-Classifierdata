import re


LOCATION_PATTERN = re.compile(r"^S?H?(?P<sheet>\d+)-(?P<zone>[A-Z0-9]+)$")
ZONE_PATTERN = re.compile(r"^[A-Z]+\d*$")
STRICT_ZONE_PATTERN = re.compile(r"^[A-Z]+\d+$")


def parse_location(location):
    text = (location or "").strip().upper().replace(" ", "")
    match = LOCATION_PATTERN.match(text)
    if not match:
        return {
            "drawing_sheet": "",
            "zone": text,
            "warnings": [f"Could not split LOCATION: {location!r}"],
        }

    zone = match.group("zone")
    corrected_zone = _correct_zone_ocr(zone)
    warnings = []
    if not ZONE_PATTERN.match(corrected_zone):
        warnings.append(f"Could not normalize LOCATION zone: {location!r}")
    elif corrected_zone != zone:
        warnings.append(f"Normalized LOCATION zone OCR confusion from {zone!r} to {corrected_zone!r}.")

    return {
        "drawing_sheet": float(match.group("sheet")),
        "zone": corrected_zone,
        "warnings": warnings,
    }


def _correct_zone_ocr(zone):
    value = (zone or "").strip().upper()
    if STRICT_ZONE_PATTERN.match(value):
        return value

    candidates = []
    for split_at in range(1, len(value)):
        letters = _correct_zone_letters(value[:split_at])
        numbers = _correct_zone_numbers(value[split_at:])
        if letters is None or numbers is None:
            continue
        candidate = letters[0] + numbers[0]
        if STRICT_ZONE_PATTERN.match(candidate):
            candidates.append((letters[1] + numbers[1], split_at, candidate))

    if not candidates:
        return value
    return min(candidates)[2]


def _correct_zone_letters(value):
    corrected = []
    changes = 0
    for char in value:
        if "A" <= char <= "Z":
            corrected.append(char)
        elif char == "0":
            corrected.append("D")
            changes += 1
        elif char == "8":
            corrected.append("B")
            changes += 1
        else:
            return None
    return "".join(corrected), changes


def _correct_zone_numbers(value):
    corrected = []
    changes = 0
    for char in value:
        if char.isdigit():
            corrected.append(char)
        elif char in {"D", "O"}:
            corrected.append("0")
            changes += 1
        elif char == "B":
            corrected.append("8")
            changes += 1
        else:
            return None
    return "".join(corrected), changes

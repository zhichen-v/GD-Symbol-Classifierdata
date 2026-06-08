import re


def parse_location(location):
    text = (location or "").strip().upper().replace(" ", "")
    match = re.match(r"^S?H?(?P<sheet>\d+)-(?P<zone>[A-Z]+\d*)$", text)
    if not match:
        return {
            "drawing_sheet": "",
            "zone": text,
            "warnings": [f"Could not split LOCATION: {location!r}"],
        }

    return {
        "drawing_sheet": float(match.group("sheet")),
        "zone": match.group("zone"),
        "warnings": [],
    }

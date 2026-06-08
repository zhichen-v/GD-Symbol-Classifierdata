import re


def resolve_equipment(characteristic, raw_specification, parsed=None):
    parsed = parsed or {}
    combined = f"{characteristic or ''} {raw_specification or ''} {parsed.get('normalized_text', '')}".upper()

    direct = _direct_equipment(combined)
    if direct:
        return direct

    if _has_thread(combined):
        return _decision("Thread Plug Gauge", "high", "Thread/tapped-hole callout.")

    if parsed.get("kind") == "gdt" or _has_gdt(combined):
        return _decision("CMM", "high", "GD&T control or datum-related geometric tolerance.")

    if parsed.get("kind") == "surface_roughness" or "SURFACE FINISH" in combined or "SURFACE ROUGHNESS" in combined:
        confidence = "high" if re.search(r"\d", raw_specification or "") else "medium"
        return _decision("Surface Roughness Tester", confidence, "Surface roughness/finish requirement.")

    if _has_visual_word(combined):
        return _decision("Visual", "high", "Appearance or workmanship requirement.")

    if parsed.get("kind") == "linear" or _has_simple_dimension_word(combined):
        confidence = "medium" if parsed.get("tolerance") in ("", None) else "high"
        reason = "Simple accessible dimensional check."
        if parsed.get("tolerance") in ("", None):
            reason += " Tolerance source is still unresolved."
        return _decision("Digital Caliper", confidence, reason)

    return _decision(
        "Digital Caliper",
        "low",
        "Fallback for dimensional requirement; feature access and tolerance context are incomplete.",
    )


def _decision(equipment, confidence, reason, alternatives=None, assumptions=None):
    return {
        "equipment": equipment,
        "confidence": confidence,
        "reason": reason,
        "alternatives": alternatives or [],
        "assumptions": assumptions or [],
    }


def _direct_equipment(text):
    direct_map = {
        "CMM": "CMM",
        "CALIPER": "Digital Caliper",
        "PLUG GAUGE": "Thread Plug Gauge",
        "THREAD GAUGE": "Thread Plug Gauge",
        "ROUGHNESS TESTER": "Surface Roughness Tester",
        "VISUAL": "Visual",
    }
    for key, equipment in direct_map.items():
        if key in text:
            return _decision(equipment, "high", f"Requirement directly names {equipment}.")
    return None


def _has_thread(text):
    return bool(re.search(r"\b(?:UNC|UNF|UNEF|NPT|BSP|THREAD|TAP|TAPPED)\b", text))


def _has_gdt(text):
    return any(
        token in text
        for token in (
            " GD ",
            "GD&T",
            "TRUE POSITION",
            "POSITION",
            "FLATNESS",
            "PARALLELISM",
            "PERPENDICULARITY",
            "PROFILE",
            "RUNOUT",
            "[GD_",
            "GD_REVIEW_REQUIRED",
            "\u2316",
            "\u23e5",
            "\u2225",
            "\u2312",
            "\u2220",
            "\u27c2",
            "\u25cb",
            "\u25ce",
            "\u232d",
            "\u232f",
            "\u2197",
            "\u2330",
        )
    )


def _has_visual_word(text):
    return any(
        token in text
        for token in (
            "CHIP",
            "CHIPOUT",
            "SCRATCH",
            "DIRT",
            "DENT",
            "BURR",
            "RUST",
            "APPEARANCE",
            "COSMETIC",
        )
    )


def _has_simple_dimension_word(text):
    return any(
        token in text
        for token in (
            "LENGTH",
            "WIDTH",
            "THICKNESS",
            "HEIGHT",
            "DIAMETER",
            "DIMENSION",
            "DEPTH",
            "SLOT",
        )
    )

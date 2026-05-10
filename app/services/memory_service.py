import re
from collections.abc import Iterable


LOCATIONS = ["thane", "mumbai", "pune", "navi mumbai", "kalyan", "dombivli"]


def extract_preferences(message: str, previous: dict | None = None) -> dict:
    prefs = dict(previous or {})
    text = message.lower()

    for location in LOCATIONS:
        if location in text:
            prefs["location"] = location
            break

    if "location" not in prefs:
        freeform_location = re.search(
            r"(?:in|from|at|near)\s+([a-z0-9\-\s,]{3,})",
            text,
        )
        if freeform_location:
            candidate = re.split(
                r"(?:\b(?:under|below|budget|for|with|rent|buy|sale|flat|apartment|house|property|properties)\b|\d+\s*bhk)",
                freeform_location.group(1),
                maxsplit=1,
            )[0].strip(" ,.-")
            if candidate:
                prefs["location"] = candidate

    bhk_match = re.search(r"(\d+)\s*bhk", text)
    if bhk_match:
        prefs["bhk"] = int(bhk_match.group(1))

    budget_match = re.search(r"(?:under|below|budget|for|around|upto|up to|max)\s*([0-9]+(?:\.[0-9]+)?)(k|l|lac|lakh)?", text)
    if budget_match:
        budget = float(budget_match.group(1))
        suffix = (budget_match.group(2) or "").lower()
        if suffix == "k":
            budget *= 1000
        elif suffix in {"l", "lac", "lakh"}:
            budget *= 100000
        prefs["budget"] = budget

    if "rent" in text:
        prefs["type"] = "rent"
    elif "buy" in text or "sale" in text:
        prefs["type"] = "buy"

    return prefs


def has_minimum_requirements(prefs: dict, required: Iterable[str]) -> bool:
    return all(key in prefs and prefs.get(key) not in ("", None, []) for key in required)

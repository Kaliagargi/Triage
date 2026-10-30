"""Conservative, deterministic extraction for disaster voice transcripts."""
import json
import os
import re
from typing import Any, Dict

FIXTURE_DIR = os.getenv("EXTRACTION_FIXTURE_DIR", "data/extraction_fixtures")
HAZARD_PATTERNS = {
    "flood": (r"\bflood(?:ed|ing)?\b", r"\bpaani\s+(?:bhar|ghus)", r"\bwater\s+(?:logging|level|entered)\b", r"\bbaadh\b", r"\bdoob"),
    "fire": (r"\bfire\b", r"\baag\s+lag", r"\bburning\b", r"\bdhua\b"),
    "building_collapse": (r"\b(building|house|wall)\s+collaps", r"\bmakaan\s+gir", r"\bimaarat\s+gir"),
    "road_blockage": (r"\broad\s+(?:blocked|blockage|closed)\b", r"\brasta\s+band\b", r"\bpul(?:iya)?\s+toot"),
}
NEED_PATTERNS = {
    "rescue": (r"\b(rescue|trapped|stranded|fasa|fas[ae]|nikal[o]?)\b", r"\bhelp\s+us\b"),
    "medical": (r"\b(medical|doctor|ambulance|injured|chot|behosh)\b",),
    "food": (r"\b(food|khana|ration)\b",),
    "drinking_water": (r"\b(drinking water|pani|water supply)\b",),
    "shelter": (r"\b(shelter|relief camp|rehne ki jagah)\b",),
    "water_pumping": (r"\b(pumping|pump out|drain water|paani nikal)\b",),
}
RELATION_PATTERNS = [
    (r"\b(?:ke\s+)?peeche\b|\bbehind\b", "behind"),
    (r"\b(?:ke\s+)?paas\b|\bnear(?:\s+to)?\b", "near"),
    (r"\b(?:ke\s+)?samne\b|\bin\s+front\s+of\b|\bopposite\b", "opposite"),
    (r"\b(?:ke\s+)?bagal\s+mein\b|\bnext\s+to\b", "near"),
]
FILLER = {"paani", "pani", "bhar", "gaya", "gayi", "hai", "hain", "ho", "raha", "rahi", "flood", "water", "is", "has", "the", "at", "near", "behind", "peeche", "paas", "ke", "of", "in"}

def load_fixture(sha256: str) -> Dict[str, Any]:
    path = os.path.join(FIXTURE_DIR, f"{sha256}.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

def _extract_anchor(text: str, relation_match: re.Match) -> str:
    tokens = re.findall(r"[\w'-]+", text[:relation_match.start()].strip(" ,.;:-").lower())
    while tokens and tokens[-1] in FILLER:
        tokens.pop()
    return " ".join(tokens[-3:]).strip()

def _valid_fixture(fixture: Dict[str, Any]) -> bool:
    required = {"hazard_type", "place_phrase", "anchor_name", "spatial_relation", "urgency", "needs", "confidence"}
    return required.issubset(fixture) and isinstance(fixture.get("needs"), list)

def extract_fields(transcript: str, sha256: str) -> Dict[str, Any]:
    """Prefer a valid curated fixture; otherwise extract only supported evidence."""
    fixture = load_fixture(sha256)
    if fixture and _valid_fixture(fixture):
        return {**fixture, "source": "fixture", "needs_review": bool(fixture.get("needs_review", False))}

    text = (transcript or "").strip()
    normalized = re.sub(r"\s+", " ", text.lower())
    hazard = next((name for name, patterns in HAZARD_PATTERNS.items() if _matches(normalized, patterns)), None)
    needs = [name for name, patterns in NEED_PATTERNS.items() if _matches(normalized, patterns)]
    urgent = _matches(normalized, (r"\burgent\b", r"\bimmediately\b", r"\bjaldi\b", r"\bplease\s+help\b", r"\btrapped\b", r"\bfas[ae]\b"))
    urgency = "high" if urgent else "medium" if hazard or needs else "unknown"

    relation, anchor, place_phrase = "", "", ""
    for pattern, canonical in RELATION_PATTERNS:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            relation = canonical
            anchor = _extract_anchor(normalized, match)
            place_phrase = normalized[max(0, match.start() - 48):match.end() + 24].strip()
            break

    water_match = re.search(r"(?:water|paani|pani)\s+(?:level\s+)?(?:is\s+)?(above\s+[^,.]+|up\s+to\s+[^,.]+|knee[- ]deep|waist[- ]deep|chest[- ]deep)", normalized)
    people_match = re.search(r"\b(\d{1,3})\s+(?:people|persons|log|bachche|children)\b", normalized)
    confidence = round(min(0.95, (0.35 if hazard else 0) + (0.20 if needs else 0) + (0.25 if anchor else 0) + (0.10 if urgency != "unknown" else 0) + (0.10 if text else 0)), 2)
    return {
        "hazard_type": hazard,
        "place_phrase": place_phrase or None,
        "anchor_name": anchor or None,
        "spatial_relation": relation or None,
        "urgency": urgency,
        "water_level_description": water_match.group(0) if water_match else None,
        "people_count": int(people_match.group(1)) if people_match else None,
        "needs": needs,
        "evidence_text": text,
        "confidence": confidence,
        "source": "rules",
        "needs_review": confidence < 0.60 or hazard is None or not anchor,
    }

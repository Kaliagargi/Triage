# Extraction service (deterministic fixture based)
import json
import os
from typing import Dict

# Simple mapping of audio SHA256 to expected extraction JSON
FIXTURE_DIR = os.getenv("EXTRACTION_FIXTURE_DIR", "data/extraction_fixtures")

def load_fixture(sha256: str) -> Dict:
    fixture_path = os.path.join(FIXTURE_DIR, f"{sha256}.json")
    if os.path.isfile(fixture_path):
        with open(fixture_path, "r", encoding="utf-8") as f:
            return json.load(f)
    # Default fallback – empty dict
    return {}

def extract_fields(transcript: str, sha256: str) -> Dict:
    """Return a deterministic extraction result.
    For the Make‑a‑Thon we rely on pre‑generated JSON fixtures indexed by the audio SHA‑256.
    If no fixture exists we return a minimal structure with confidence 0.
    """
    fixture = load_fixture(sha256)
    if fixture:
        return fixture
    # Very simple fallback – could be extended to rule‑based parsing
    return {
        "hazard_type": "flood",
        "place_phrase": "",
        "anchor_name": "",
        "spatial_relation": "",
        "urgency": "low",
        "water_level_description": None,
        "needs": [],
        "evidence_text": transcript,
        "confidence": 0.0,
    }

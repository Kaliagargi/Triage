import json

from backend.services.extraction import extract_fields


def test_unknown_transcript_does_not_default_to_flood(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTRACTION_FIXTURE_DIR", str(tmp_path))
    result = extract_fields("The road is clear today.", "missing-fixture")
    assert result["hazard_type"] is None
    assert result["needs_review"] is True
    assert result["confidence"] < 0.60


def test_hinglish_flood_landmark_phrase(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTRACTION_FIXTURE_DIR", str(tmp_path))
    result = extract_fields(
        "Paani bhar gaya hai kali mandir ke peeche, rescue chahiye.",
        "no-fixture",
    )
    assert result["hazard_type"] == "flood"
    assert result["anchor_name"] == "kali mandir"
    assert result["spatial_relation"] == "behind"
    assert "rescue" in result["needs"]
    assert result["needs_review"] is False


def test_valid_fixture_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTRACTION_FIXTURE_DIR", str(tmp_path))
    fixture = {
        "hazard_type": "fire", "place_phrase": "near school",
        "anchor_name": "school", "spatial_relation": "near",
        "urgency": "high", "needs": ["rescue"], "confidence": 0.9,
    }
    (tmp_path / "abc.json").write_text(json.dumps(fixture), encoding="utf-8")
    result = extract_fields("Paani bhar gaya", "abc")
    assert result["source"] == "fixture"
    assert result["hazard_type"] == "fire"

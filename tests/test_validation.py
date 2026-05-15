"""Layer 1: schema-validation logic. No API calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lean_alpha.specialists import _validate_artifact_json, citation_pct


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "A2_1.json"
    p.write_text(json.dumps(payload))
    return p


def _good_payload() -> dict:
    return {
        "stage": "A2.1",
        "summary": "Three drivers identified.",
        "rows": [
            {
                "label": "Revenue growth",
                "value": "+15% CAGR",
                "source_url": "https://example.com/x",
                "confidence": "high",
                "assumed": False,
            },
        ],
    }


def test_validates_well_formed_payload(tmp_path):
    path = _write(tmp_path, _good_payload())
    out = _validate_artifact_json("A2.1", path)
    assert out["stage"] == "A2.1"


def test_rejects_invalid_json(tmp_path):
    p = tmp_path / "A2_1.json"
    p.write_text("not json {")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        _validate_artifact_json("A2.1", p)


def test_rejects_wrong_stage(tmp_path):
    bad = _good_payload()
    bad["stage"] = "A4.1"
    with pytest.raises(RuntimeError, match="stage field is"):
        _validate_artifact_json("A2.1", _write(tmp_path, bad))


def test_rejects_missing_summary(tmp_path):
    bad = _good_payload()
    bad["summary"] = ""
    with pytest.raises(RuntimeError, match="`summary`"):
        _validate_artifact_json("A2.1", _write(tmp_path, bad))


def test_rejects_empty_rows(tmp_path):
    bad = _good_payload()
    bad["rows"] = []
    with pytest.raises(RuntimeError, match="`rows`"):
        _validate_artifact_json("A2.1", _write(tmp_path, bad))


def test_rejects_bad_confidence(tmp_path):
    bad = _good_payload()
    bad["rows"][0]["confidence"] = "very-high"
    with pytest.raises(RuntimeError, match="confidence"):
        _validate_artifact_json("A2.1", _write(tmp_path, bad))


def test_rejects_non_bool_assumed(tmp_path):
    bad = _good_payload()
    bad["rows"][0]["assumed"] = "no"
    with pytest.raises(RuntimeError, match="`assumed`"):
        _validate_artifact_json("A2.1", _write(tmp_path, bad))


def test_citation_pct_basic():
    payload = _good_payload()
    payload["rows"] = [
        {"label": "a", "value": "1", "source_url": "https://x", "confidence": "high", "assumed": False},
        {"label": "b", "value": "2", "source_url": None, "confidence": "low", "assumed": True},
        {"label": "c", "value": "3", "source_url": "https://y", "confidence": "medium", "assumed": False},
    ]
    assert citation_pct(payload) == pytest.approx(66.66, rel=0.01)


def test_citation_pct_assumed_overrides_url():
    """A row with a source_url but assumed=True should NOT count as cited."""
    payload = {
        "rows": [
            {"label": "a", "value": "1", "source_url": "https://x", "confidence": "high", "assumed": True},
        ]
    }
    assert citation_pct(payload) == 0.0


def test_citation_pct_empty():
    assert citation_pct({"rows": []}) == 0.0

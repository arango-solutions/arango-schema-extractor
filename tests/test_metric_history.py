"""Tests for quality-metric history (PRD §3.12.3)."""

from __future__ import annotations

from schema_analyzer.metric_history import (
    append_to_history,
    load_history,
    metric_snapshot,
    record_metrics,
    save_history,
    summarize_history,
)


def _analysis(camel: bool, health, confidence, run_id="r1"):
    """Build an analysis dict in either camelCase (tool response) or snake_case
    (model_dump) metadata key style."""
    if camel:
        meta = {
            "runId": run_id,
            "analysisCompletedAt": "2026-01-01T00:00:00Z",
            "physicalSchemaFingerprint": "fp",
            "confidence": confidence,
            "healthScore": health,
            "usedBaseline": False,
            "qualityMetrics": {
                "structural": {"orphanEntityRatio": 0.25, "danglingRelationshipRatio": 0.0},
                "grounding": {"mappingGroundingRatio": 1.0},
                "gold": {"overlap": 0.8},
            },
        }
    else:
        meta = {
            "run_id": run_id,
            "analysis_completed_at": "2026-01-01T00:00:00Z",
            "physical_schema_fingerprint": "fp",
            "confidence": confidence,
            "health_score": health,
            "used_baseline": True,
            "quality_metrics": {
                "structural": {"orphanEntityRatio": 0.25, "danglingRelationshipRatio": 0.0},
                "grounding": {"mappingGroundingRatio": 1.0},
            },
        }
    return {"conceptualSchema": {}, "physicalMapping": {}, "metadata": meta}


def test_snapshot_camel_keys():
    snap = metric_snapshot(_analysis(camel=True, health=82, confidence=0.8))
    assert snap["runId"] == "r1"
    assert snap["healthScore"] == 82
    assert snap["confidence"] == 0.8
    assert snap["mappingGroundingRatio"] == 1.0
    assert snap["goldOverlap"] == 0.8
    assert snap["usedBaseline"] is False


def test_snapshot_snake_keys():
    snap = metric_snapshot(_analysis(camel=False, health=24, confidence=0.1))
    assert snap["runId"] == "r1"
    assert snap["healthScore"] == 24
    assert snap["usedBaseline"] is True
    assert snap["goldOverlap"] is None  # no gold block


def test_append_caps_history():
    hist: list = []
    for i in range(5):
        hist = append_to_history(hist, {"healthScore": i}, max_entries=3)
    assert [s["healthScore"] for s in hist] == [2, 3, 4]


def test_append_does_not_mutate_input():
    hist = [{"healthScore": 1}]
    out = append_to_history(hist, {"healthScore": 2})
    assert len(hist) == 1
    assert len(out) == 2


def test_summarize_trend():
    history = [
        {"timestamp": "t0", "healthScore": 60, "confidence": 0.5, "goldOverlap": 0.6},
        {"timestamp": "t1", "healthScore": 70, "confidence": 0.6, "goldOverlap": 0.7},
        {"timestamp": "t2", "healthScore": 90, "confidence": 0.9, "goldOverlap": 0.9},
    ]
    summary = summarize_history(history)
    assert summary["runCount"] == 3
    assert summary["firstTimestamp"] == "t0"
    assert summary["lastTimestamp"] == "t2"
    assert summary["healthScore"]["min"] == 60
    assert summary["healthScore"]["max"] == 90
    assert summary["healthScore"]["latest"] == 90
    assert summary["healthScore"]["delta"] == 30
    assert summary["goldOverlap"]["delta"] == round(0.9 - 0.6, 4)


def test_summarize_empty():
    assert summarize_history([]) == {"runCount": 0}


def test_summarize_missing_series_is_none():
    summary = summarize_history([{"timestamp": "t0"}])
    assert summary["healthScore"]["mean"] is None


def test_filesystem_roundtrip(tmp_path):
    path = tmp_path / "history.json"
    s1 = record_metrics(path, _analysis(camel=True, health=80, confidence=0.8, run_id="a"))
    s2 = record_metrics(path, _analysis(camel=True, health=90, confidence=0.9, run_id="b"))
    assert s1["runId"] == "a" and s2["runId"] == "b"
    loaded = load_history(path)
    assert [s["runId"] for s in loaded] == ["a", "b"]
    assert summarize_history(loaded)["healthScore"]["latest"] == 90


def test_load_missing_and_corrupt(tmp_path):
    assert load_history(tmp_path / "nope.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_history(bad) == []


def test_save_accepts_legacy_list_shape(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text('[{"runId": "x", "healthScore": 50}]', encoding="utf-8")
    loaded = load_history(path)
    assert loaded == [{"runId": "x", "healthScore": 50}]
    # Re-save normalizes to the {schemaVersion, history} shape.
    save_history(path, loaded)
    assert load_history(path) == loaded

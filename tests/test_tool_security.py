"""Tests for the v1 contract / operator-side hardening introduced in Phase 1.

Covers:

* H1 — ``outputOptions.pretty`` removed from the request schema
* H2 — ``outputOptions.includeSnapshotFingerprint`` actually controls the
       ``tooling.snapshotFingerprint`` field
* H3 — string-length / array-size caps in ``request.schema.json`` reject
       oversize inputs
* M2 — ``SCHEMA_ANALYZER_CACHE_ROOT`` rejects out-of-root cache directories
* M3 — ``SCHEMA_ANALYZER_ALLOWED_HOSTS`` rejects hosts not on the allowlist
"""

from __future__ import annotations

import pytest

from schema_analyzer.cache import _resolve_cache_directory, cache_from_config
from schema_analyzer.errors import SchemaAnalyzerError
from schema_analyzer.tool import _check_url_allowed, _tooling_block, run_tool
from schema_analyzer.tool_contract_v1 import validate_request_v1

# ---------- H1 ----------


def test_h1_pretty_field_rejected_by_schema():
    errs = validate_request_v1(
        {
            "contractVersion": "1",
            "operation": "docs",
            "input": {
                "analysis": {
                    "conceptualSchema": {"entities": [], "relationships": [], "properties": []},
                    "physicalMapping": {"entities": {}, "relationships": {}},
                    "metadata": {
                        "confidence": 1.0,
                        "timestamp": "2026-01-01T00:00:00Z",
                        "analyzedCollectionCounts": {
                            "documentCollections": 0,
                            "edgeCollections": 0,
                        },
                        "detectedPatterns": [],
                    },
                }
            },
            "outputOptions": {"pretty": True},
        }
    )
    assert any("pretty" in m or "additional propert" in m.lower() for m in errs), errs


# ---------- H2 ----------


def test_h2_tooling_includes_snapshot_fingerprint_by_default():
    snap = {"version": 1, "collections": []}
    block = _tooling_block(analysis=None, snapshot=snap)
    assert "snapshotFingerprint" in block


def test_h2_tooling_omits_snapshot_fingerprint_when_disabled():
    snap = {"version": 1, "collections": []}
    block = _tooling_block(analysis=None, snapshot=snap, include_snapshot_fingerprint=False)
    assert "snapshotFingerprint" not in block
    assert block["snapshotVersion"] == 1


# ---------- H3 ----------


def test_h3_oversize_url_rejected_by_schema():
    huge = "http://" + ("a" * 3000) + ".example/"
    errs = validate_request_v1(
        {
            "contractVersion": "1",
            "operation": "snapshot",
            "connection": {
                "url": huge,
                "database": "db",
                "passwordEnvVar": "X",
            },
        }
    )
    assert any("maxLength" in m or "is too long" in m for m in errs), errs


def test_h3_timeout_above_cap_rejected_by_schema():
    errs = validate_request_v1(
        {
            "contractVersion": "1",
            "operation": "snapshot",
            "connection": {
                "url": "http://localhost:8529",
                "database": "db",
                "passwordEnvVar": "X",
            },
            "analysisOptions": {"timeoutMs": 99_999_999},
        }
    )
    assert any("maximum" in m or "less than" in m for m in errs), errs


# ---------- M2 ----------


def test_m2_cache_root_rejects_outside_directory(tmp_path, monkeypatch):
    root = tmp_path / "cache_root"
    outside = tmp_path / "elsewhere" / "x"
    monkeypatch.setenv("SCHEMA_ANALYZER_CACHE_ROOT", str(root))
    with pytest.raises(SchemaAnalyzerError) as exc:
        _resolve_cache_directory(str(outside))
    assert exc.value.code == "INVALID_ARGUMENT"


def test_m2_cache_root_accepts_inside_directory(tmp_path, monkeypatch):
    root = tmp_path / "cache_root"
    inside = root / "deep" / "nested"
    monkeypatch.setenv("SCHEMA_ANALYZER_CACHE_ROOT", str(root))
    resolved = _resolve_cache_directory(str(inside))
    assert resolved.is_relative_to(root.resolve())


def test_m2_cache_root_blocks_dotdot_traversal(tmp_path, monkeypatch):
    root = tmp_path / "cache_root"
    monkeypatch.setenv("SCHEMA_ANALYZER_CACHE_ROOT", str(root))
    sneaky = root / ".." / "outside"
    with pytest.raises(SchemaAnalyzerError):
        _resolve_cache_directory(str(sneaky))


def test_m2_cache_unset_env_preserves_legacy_behavior(tmp_path, monkeypatch):
    monkeypatch.delenv("SCHEMA_ANALYZER_CACHE_ROOT", raising=False)
    cache = cache_from_config({"type": "filesystem", "directory": str(tmp_path / "anywhere")})
    assert cache is not None


# ---------- M3 ----------


def test_m3_unset_env_allows_any_host(monkeypatch):
    monkeypatch.delenv("SCHEMA_ANALYZER_ALLOWED_HOSTS", raising=False)
    _check_url_allowed("http://anything.example:8529")


def test_m3_allowed_host_accepted(monkeypatch):
    monkeypatch.setenv("SCHEMA_ANALYZER_ALLOWED_HOSTS", "db.internal:8529,other.example")
    _check_url_allowed("http://db.internal:8529/")


def test_m3_disallowed_host_rejected(monkeypatch):
    monkeypatch.setenv("SCHEMA_ANALYZER_ALLOWED_HOSTS", "db.internal:8529")
    with pytest.raises(SchemaAnalyzerError) as exc:
        _check_url_allowed("http://evil.example:8529/")
    assert exc.value.code == "INVALID_ARGUMENT"


def test_m3_allowlist_blocks_run_tool_request(monkeypatch):
    monkeypatch.setenv("SCHEMA_ANALYZER_ALLOWED_HOSTS", "trusted.local")
    monkeypatch.setenv("X_PASS", "pw")
    resp = run_tool(
        {
            "contractVersion": "1",
            "operation": "snapshot",
            "connection": {
                "url": "http://untrusted.example:8529",
                "database": "db",
                "passwordEnvVar": "X_PASS",
            },
        }
    )
    assert resp["ok"] is False
    assert resp["error"]["code"] == "INVALID_ARGUMENT"
    assert "allowlist" in resp["error"]["message"].lower()

from __future__ import annotations

from schema_analyzer import defaults


def test_defaults_are_sensible():
    assert defaults.MAX_REPAIR_ATTEMPTS >= 1
    assert defaults.MAX_RETRIES >= 1
    assert defaults.RETRY_BASE_DELAY > 0
    assert 0.0 <= defaults.LLM_TEMPERATURE <= 1.0
    assert defaults.ANTHROPIC_MAX_TOKENS > 0
    assert 0.0 < defaults.CONFIDENCE_BASE <= 1.0
    assert defaults.DEFAULT_TIMEOUT_MS > 0
    assert defaults.DEFAULT_CACHE_TTL_SECONDS > 0
    assert defaults.SAMPLE_VALUE_TOP_K > 0
    assert isinstance(defaults.DEFAULT_CACHE_DIR, str)

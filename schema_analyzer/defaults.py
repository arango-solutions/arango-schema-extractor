"""
Central location for tunable defaults that were previously scattered as magic numbers.

Import from here instead of hard-coding values in individual modules.
"""

from __future__ import annotations

# LLM workflow
MAX_REPAIR_ATTEMPTS: int = 2
MAX_RETRIES: int = 2
RETRY_BASE_DELAY: float = 1.0

# LLM sampling (provider-level)
LLM_TEMPERATURE: float = 0.0
ANTHROPIC_MAX_TOKENS: int = 4096

# Analysis
CONFIDENCE_BASE: float = 0.9
CONFIDENCE_WARNING_PENALTY: float = 0.05
CONFIDENCE_MAX_PENALTY: float = 0.6
CONFIDENCE_FLOOR: float = 0.1
MIN_LLM_BUDGET_MS: int = 1_000
DEFAULT_TIMEOUT_MS: int = 60_000
DEFAULT_REVIEW_THRESHOLD: float = 0.6
DEFAULT_CACHE_TTL_SECONDS: int = 86_400

# Snapshot
SAMPLE_VALUE_TOP_K: int = 20

# Cache
DEFAULT_CACHE_DIR: str = ".schema-analyzer-cache"

# Baseline inference
BASELINE_NO_LLM_CONFIDENCE: float = 0.1
MIN_TYPE_FIELD_DISTINCT_VALUES: int = 2
UNRESOLVED_ENDPOINT: str = "Any"

# Eval harness
DEFAULT_EVAL_SAMPLE_LIMIT: int = 3
DEFAULT_EVAL_SCALE: int = 5
DEFAULT_EVAL_SEED: int = 1
EVAL_DELTA_THRESHOLD: float = 0.005

# Provider / network
OPENROUTER_ERROR_BODY_MAX_CHARS: int = 2000
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

# Fingerprint display
FINGERPRINT_DISPLAY_LENGTH: int = 16

# CLI / tool defaults
DEFAULT_ARANGO_URL: str = "http://localhost:8529"
DEFAULT_ARANGO_USER: str = "root"
DEFAULT_EVAL_DATABASE: str = "schema_analyzer_eval"
TOOL_ERROR_EXIT_CODE: int = 2
FALLBACK_LIBRARY_VERSION: str = "0.0.0-dev"
DEFAULT_EXPORT_TARGET: str = "cypher"

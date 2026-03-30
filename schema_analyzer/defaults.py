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

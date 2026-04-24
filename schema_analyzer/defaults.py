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
# Optional environment variable that, when set, restricts every
# resolved cache directory to a subtree rooted at its value. Lets
# operators expose the v1 tool over an MCP/RPC surface without
# letting callers write under arbitrary filesystem paths via
# ``analysisOptions.cache.directory``.
CACHE_ROOT_ENV_VAR: str = "SCHEMA_ANALYZER_CACHE_ROOT"

# Connection trust
# Optional environment variable (comma-separated host[:port] list)
# that, when set, restricts the host portion of ``connection.url``
# accepted by ``run_tool``. Empty / unset preserves the historical
# trust-the-caller behaviour for local CLI use.
ALLOWED_HOSTS_ENV_VAR: str = "SCHEMA_ANALYZER_ALLOWED_HOSTS"

# Baseline inference
BASELINE_NO_LLM_CONFIDENCE: float = 0.1
MIN_TYPE_FIELD_DISTINCT_VALUES: int = 2
# Fields with more distinct values than this are rejected as discriminators
# (they are almost always ID-like, not type labels).
MAX_TYPE_FIELD_DISTINCT_VALUES: int = 32
# Minimum fraction of documents whose values fall within the observed top-K
# distinct values for the field to count as a genuine discriminator.
MIN_TYPE_FIELD_COVERAGE_FRACTION: float = 0.80
# Discriminator values must be strings of at most this length and match the
# pattern [A-Za-z0-9_-]+. Longer values look like content, not type labels.
MAX_TYPE_VALUE_LENGTH: int = 64
# Cap on the number of non-allow-listed candidate fields probed per collection
# during snapshot COLLECT-based discriminator detection, to bound AQL cost.
MAX_BROADENED_TYPE_CANDIDATES: int = 10
UNRESOLVED_ENDPOINT: str = "Any"

# Shard-family detection (PRD §6.2 bullet 5 — see docs/PRD.md)
# A shard family groups structurally-identical entities whose names
# share a common suffix (e.g. ``IBEXDocument`` / ``MAROCCHINODocument``
# / ``MOR1KXDocument`` / ``OR1200Document`` → family ``Document``).
# Buckets smaller than this are skipped — a "family of one" is just an
# entity.
MIN_SHARD_FAMILY_SIZE: int = 2
# Minimum length of the common suffix used to label a family. Shorter
# suffixes (``Op``, ``Tx``) trigger too many false positives across
# unrelated entities. The suffix must end on a capital-letter boundary
# (i.e. the character just before the suffix is lower-case or the
# suffix starts at index 0); this gates against accidental substring
# matches like ``Tenant`` matching ``CurrentTenant`` and
# ``ParentTenant`` while rejecting ``MultitenantConfig``.
MIN_SHARD_FAMILY_SUFFIX_LEN: int = 4
# Discriminator field probe: when every member collection of a family
# carries one of these field names, the family is annotated with
# ``discriminator.source = "field"``. Otherwise the discriminator
# falls back to ``"collection_prefix"`` (the prefix portion of each
# member's conceptual name).
SHARD_FAMILY_DISCRIMINATOR_FIELDS: tuple[str, ...] = (
    "repo",
    "source",
    "stream",
    "upstream",
)

# Multitenancy detection (PRD §6.2 bullet 4 — see docs/PRD.md)
# Threshold for `discriminator_field` style: a candidate property
# (e.g. ``tenantId``, ``org_id``) is treated as a tenant-discriminator
# only when it appears in at least this fraction of the analysed
# user collections. Set conservatively — a one-off ``tenantId`` field
# on a single audit collection should NOT trigger discriminator-style
# multitenancy classification.
MIN_TENANT_FIELD_COVERAGE_FRACTION: float = 0.5
# Cap on the number of distinct tenant identifiers reported per
# collection. We only need a small sample for evidence; large
# tenant pools (thousands of distinct values) would bloat the
# response and hurt fingerprint stability.
MAX_TENANT_DISTINCT_VALUES: int = 50
# Candidate tenant-discriminator property names probed in order
# (case-insensitive). The first name carried by enough collections
# wins. Order reflects ArangoDB community convention plus widely-used
# SaaS naming patterns. Operators can override via
# ``Analyzer(tenant_discriminator_fields=...)``.
TENANT_DISCRIMINATOR_FIELDS: tuple[str, ...] = (
    "tenantId",
    "tenant_id",
    "TENANT_ID",
    "tenant",
    "orgId",
    "org_id",
    "organizationId",
    "accountId",
    "account_id",
    "customerId",
    "workspaceId",
)
# Collection-per-tenant naming pattern (PRD §6.2 bullet 4, case 4).
# A regex with two named groups: ``base`` (the conceptual base name)
# and ``tenant`` (the per-tenant discriminator). Both groups must
# contain at least 2 chars to suppress trivial matches like
# ``a__b``.
TENANT_COLLECTION_NAMING_PATTERNS: tuple[str, ...] = (
    r"^(?P<base>[A-Za-z][A-Za-z0-9]+)__(?P<tenant>[A-Za-z0-9][A-Za-z0-9_-]+)$",
    r"^(?P<tenant>[A-Za-z0-9][A-Za-z0-9_-]+?)_(?P<base>[A-Z][A-Za-z0-9]+)$",
)
# Database-naming pattern that hints "this snapshot is one tenant
# of a database-per-tenant deployment". Single-database scope means
# we cannot prove the pattern from one snapshot alone — at best we
# can flag the result as ``unknown_single_db`` so an orchestrator
# aggregating multiple databases can confirm.
TENANT_DATABASE_NAMING_PATTERNS: tuple[str, ...] = (
    r"^tenant[_-]?[A-Za-z0-9]+$",
    r"^[A-Za-z0-9]+[_-]tenant$",
)

# Tenant-scope annotator (issue #13)
# Conceptual entity names treated as tenant roots. First match in the
# tuple that exists in the schema becomes the canonical tenant root.
TENANT_SCOPE_ROOT_NAMES: tuple[str, ...] = ("Tenant",)
# Regex that identifies a denormalised tenant-reference field on
# non-root entities. Default matches TENANT_ID, tenant_id, tenantId,
# tenant_key, TENANT-ID. Compiled case-insensitively at use site.
TENANT_SCOPE_FIELD_REGEX: str = r"^tenant[_-]?(id|key)$"
# BFS depth cap when deciding whether a non-denorm entity is reachable
# from the tenant root over the conceptual relationship graph. Used to
# distinguish "tenant_scoped via traversal" from "global metadata".
TENANT_SCOPE_MAX_HOPS: int = 5

# Eval harness
DEFAULT_EVAL_SAMPLE_LIMIT: int = 3
DEFAULT_EVAL_SCALE: int = 5
DEFAULT_EVAL_SEED: int = 1
EVAL_DELTA_THRESHOLD: float = 0.005

# Provider / network
OPENROUTER_ERROR_BODY_MAX_CHARS: int = 2000
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
DEFAULT_OPENAI_MODEL: str = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL: str = "claude-3-5-sonnet-latest"
DEFAULT_OPENROUTER_MODEL: str = "openai/gpt-4o-mini"

# Statistics (PRD §6.2 statistics block)
# Average degree above this is treated as the high-cardinality side
# of a relationship (drives the 1:1 / 1:N / N:1 / N:M classification
# emitted on every metadata.statistics block).
STATISTICS_CARDINALITY_THRESHOLD: float = 1.5
# Decimal places used when rounding average in/out degree.
STATISTICS_DEGREE_ROUND: int = 2
# Decimal places used when rounding selectivity (edge_count / (S × T)).
STATISTICS_SELECTIVITY_ROUND: int = 6

# Snapshot format
# Bumped when the on-disk shape of snapshot_physical_schema() changes
# in a way that fingerprint-keyed caches must invalidate.
SNAPSHOT_FORMAT_VERSION: int = 1

# OWL Turtle export
# Default IRIs used by ``export_conceptual_model_as_owl_turtle``.
# Callers can override per-call; consolidating them here makes the
# project namespace tunable without editing the export module.
DEFAULT_OWL_BASE_IRI: str = "http://arangodb.com/schema/hybrid#"
DEFAULT_OWL_PHYSICAL_IRI: str = "http://arangodb.com/schema/physical#"

# Fingerprint display
FINGERPRINT_DISPLAY_LENGTH: int = 16

# CLI / tool defaults
DEFAULT_ARANGO_URL: str = "http://localhost:8529"
DEFAULT_ARANGO_USER: str = "root"
DEFAULT_EVAL_DATABASE: str = "schema_analyzer_eval"
TOOL_ERROR_EXIT_CODE: int = 2
FALLBACK_LIBRARY_VERSION: str = "0.0.0-dev"
DEFAULT_EXPORT_TARGET: str = "cypher"

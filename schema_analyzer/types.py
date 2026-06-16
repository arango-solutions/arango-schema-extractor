from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AnalysisMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: str
    analyzed_collection_counts: dict[str, int] = Field(alias="analyzedCollectionCounts")
    detected_patterns: list[str] = Field(alias="detectedPatterns")
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    review_required: bool = Field(default=False, alias="reviewRequired")
    # Optional tool/agent observability fields (v0.1+). These are safe because metadata
    # is allowed to have additional properties by the JSON Schema validator.
    provider: str | None = None
    model: str | None = None
    repair_attempts: int = Field(default=0, alias="repairAttempts")
    used_baseline: bool = Field(default=False, alias="usedBaseline")
    # Provenance (§3.13 PRD): per-request identity, timing, physical schema linkage.
    run_id: str | None = Field(default=None, alias="runId")
    analysis_started_at: str | None = Field(default=None, alias="analysisStartedAt")
    analysis_completed_at: str | None = Field(default=None, alias="analysisCompletedAt")
    physical_schema_fingerprint: str | None = Field(default=None, alias="physicalSchemaFingerprint")
    cache_hit: bool = Field(default=False, alias="cacheHit")
    prompt_version: str | None = Field(default=None, alias="promptVersion")
    detected_domain: str | None = Field(default=None, alias="detectedDomain")
    detected_domain_confidence: float | None = Field(default=None, alias="detectedDomainConfidence")
    # Populated by the post-LLM reconciliation step (issue #5) when the
    # analyzer had to backfill collections the LLM omitted. Absent when the
    # LLM output already covered every snapshot collection.
    reconciliation: dict[str, Any] | None = Field(default=None)
    # Populated by the per-relationship cost statistics pass (issue #3) when
    # a live DB handle is available. ``statistics_status`` reports the
    # computation outcome (``"ok"``, ``"partial"``, ``"skipped_no_db"``)
    # so callers can reason about completeness even when ``statistics`` is
    # absent.
    statistics: dict[str, Any] | None = Field(default=None)
    statistics_status: str | None = Field(default=None, alias="statisticsStatus")
    # Populated by the tenant-scope annotator (issue #13) when a
    # ``Tenant`` (or configured tenant root) entity is detected. Carries
    # the per-run summary that mirrors the per-entity ``tenantScope``
    # blocks now stamped under ``physicalMapping.entities[*]``. Absent
    # on single-tenant graphs.
    tenant_scope_report: dict[str, Any] | None = Field(default=None, alias="tenantScopeReport")
    # Populated by the sharding-profile classifier (PRD §6.2 bullet 3)
    # once per analysis. Carries the primary style classification
    # (``OneShard`` / ``DisjointSmartGraph`` / ``SmartGraph`` /
    # ``SatelliteGraph`` / ``Sharded``) plus per-graph and
    # per-collection evidence. ``sharding_profile_status`` mirrors the
    # inner ``status`` field (``"ok"`` / ``"degraded"``) so callers can
    # branch on a single top-level key. Both absent when the snapshot
    # is too minimal to classify (no collections).
    sharding_profile: dict[str, Any] | None = Field(default=None, alias="shardingProfile")
    sharding_profile_status: str | None = Field(default=None, alias="shardingProfileStatus")
    # Populated by the multitenancy classifier (PRD §6.2 bullet 4) once
    # per analysis. Carries the tenancy-style classification
    # (``none`` / ``disjoint_smartgraph`` / ``shard_key`` /
    # ``discriminator_field`` / ``collection_per_tenant`` /
    # ``unknown_single_db``), the inferred tenant key, and per-collection
    # evidence. ``multitenancy_status`` mirrors the inner ``status`` field
    # (``"ok"`` / ``"degraded"``) so callers can branch on a single
    # top-level key. Both absent when the snapshot has no user collections.
    multitenancy: dict[str, Any] | None = Field(default=None, alias="multitenancy")
    multitenancy_status: str | None = Field(default=None, alias="multitenancyStatus")
    # Populated by the Arango product detector (arango_products.py).
    # Carries any first-party Arango product artefacts found in the
    # snapshot (today: Autograph corpus + KG projects). Empty
    # ``arango_product`` + ``arango_product_status="none"`` when no
    # product was detected. ``status="ok"`` with a populated
    # ``arango_product`` block when one or more projects were found.
    arango_product: dict[str, Any] | None = Field(default=None, alias="arangoProduct")
    arango_product_status: str | None = Field(default=None, alias="arangoProductStatus")
    # Populated by the quality-metrics pass (PRD §3.12.3). ``quality_metrics``
    # carries deterministic structural (connectivity, orphan ratio, property
    # richness, consistency flags) and grounding (mapping-vs-snapshot
    # faithfulness) signals. ``health_score`` is a normalized 0–100 composite
    # that folds those signals together with ``confidence`` so consumers can
    # gate on a single scalar while still being able to drill into the
    # contributing components.
    quality_metrics: dict[str, Any] | None = Field(default=None, alias="qualityMetrics")
    health_score: int | None = Field(default=None, alias="healthScore")


class AnalysisResult(BaseModel):
    conceptual_schema: dict[str, Any]
    physical_mapping: dict[str, Any]
    metadata: AnalysisMetadata


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

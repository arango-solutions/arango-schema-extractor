from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EntityMappingStyle = Literal["COLLECTION", "LABEL"]
RelationshipMappingStyle = Literal["DEDICATED_COLLECTION", "GENERIC_WITH_TYPE"]


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


class AnalysisResult(BaseModel):
    conceptual_schema: dict[str, Any]
    physical_mapping: dict[str, Any]
    metadata: AnalysisMetadata


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

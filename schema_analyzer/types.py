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


class AnalysisResult(BaseModel):
    conceptual_schema: dict[str, Any]
    physical_mapping: dict[str, Any]
    metadata: AnalysisMetadata


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


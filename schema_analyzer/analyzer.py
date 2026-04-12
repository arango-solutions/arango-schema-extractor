from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

if TYPE_CHECKING:
    from arango.database import StandardDatabase

from .baseline import infer_baseline_from_snapshot
from .cache import AnalysisCache, cache_from_config
from .conceptual import ConceptualSchema
from .defaults import (
    CONFIDENCE_BASE,
    CONFIDENCE_FLOOR,
    CONFIDENCE_MAX_PENALTY,
    CONFIDENCE_WARNING_PENALTY,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_REVIEW_THRESHOLD,
    DEFAULT_TIMEOUT_MS,
    MAX_REPAIR_ATTEMPTS,
    MIN_LLM_BUDGET_MS,
)
from .errors import SchemaAnalyzerError
from .mapping import PhysicalMapping
from .providers import create_provider, get_default_model, get_provider_env_var
from .snapshot import fingerprint_physical_schema, snapshot_physical_schema
from .types import AnalysisMetadata, AnalysisResult, now_iso
from .utils import analysis_cache_storage_key, stable_dumps
from .workflow import async_generate_validate_repair, run_generate_validate_repair

logger = logging.getLogger(__name__)

_PROVENANCE_CACHE_STRIP = (
    "run_id",
    "analysis_started_at",
    "analysis_completed_at",
    "physical_schema_fingerprint",
    "cache_hit",
    "prompt_version",
)


def _strip_provenance_for_cache(d: dict[str, Any]) -> dict[str, Any]:
    out = dict(d)
    md = dict(out.get("metadata") or {})
    for k in _PROVENANCE_CACHE_STRIP:
        md.pop(k, None)
    out["metadata"] = md
    return out


def _default_system_prompt() -> str:
    return (
        "You are a schema analysis engine. Return ONLY a single JSON object matching the provided schema. "
        "Do not include any markdown fences, explanations, or extra text."
    )


def _build_prompt(snapshot: dict[str, Any]) -> str:
    snapshot_json = stable_dumps(snapshot)
    return (
        "You will be given an ArangoDB physical schema snapshot JSON.\n"
        "Your job: infer a conceptual schema and a conceptual→physical mapping.\n\n"
        "Return ONLY a single JSON object with EXACTLY these top-level keys:\n"
        "- conceptualSchema\n"
        "- physicalMapping\n"
        "- metadata\n\n"
        "Required JSON shape (example skeleton; fill it in):\n"
        "{\n"
        '  "conceptualSchema": {\n'
        '    "entities": [{"name":"EntityType","labels":["EntityType"],'
        '"properties":[{"name":"prop"}]}],\n'
        '    "relationships": [{"type":"REL_TYPE","fromEntity":"EntityType",'
        '"toEntity":"EntityType","properties":[{"name":"prop"}]}],\n'
        '    "properties": []\n'
        "  },\n"
        '  "physicalMapping": {\n'
        '    "entities": {"EntityType":{"style":"COLLECTION","collectionName":"collection"}},\n'
        '    "relationships": {"REL_TYPE":{"style":"DEDICATED_COLLECTION","edgeCollectionName":"edges"}}\n'
        "  },\n"
        '  "metadata": {\n'
        '    "confidence": 0.0,\n'
        '    "timestamp": "ISO-8601 string",\n'
        '    "analyzedCollectionCounts": {"documentCollections": 0, "edgeCollections": 0},\n'
        '    "detectedPatterns": [],\n'
        '    "warnings": [],\n'
        '    "assumptions": []\n'
        "  }\n"
        "}\n\n"
        "Mapping styles vocabulary:\n"
        "- Entity mapping style: COLLECTION | LABEL\n"
        "- Relationship mapping style: DEDICATED_COLLECTION | GENERIC_WITH_TYPE\n\n"
        "Important:\n"
        "- Prefer entity/relationship names that match collection names, "
        "type-field values, and edge collection names found in the snapshot.\n"
        "- Always include non-empty arrays for conceptualSchema.entities "
        "and conceptualSchema.relationships if any are inferable.\n\n"
        "Per-collection entity rule (CRITICAL):\n"
        "- If you see document collections that represent distinct entity "
        "types (i.e. NOT a single generic 'entities' collection with many "
        "type values), then EVERY document collection should become an "
        "entity type.\n"
        "- Use collection.inferred_entity_type as the entity name and "
        "create a physicalMapping.entities entry with style=COLLECTION "
        "and collectionName=<collection.name>.\n\n"
        "Generic edge collection rule (CRITICAL):\n"
        "- If an edge collection has sample_field_value_counts for a "
        "field like 'relation'/'relType'/'type', then EACH DISTINCT "
        "VALUE is a relationship type.\n"
        "- For those, add a conceptualSchema.relationships entry with "
        "type=<value> and add a physicalMapping.relationships entry "
        "mapping that type to GENERIC_WITH_TYPE on that edge collection "
        "and typeField=<field> typeValue=<value>.\n\n"
        "Generic entity collection rule:\n"
        "- If a document collection has sample_field_value_counts for "
        "a field like 'type'/'kind'/'entityType', then EACH DISTINCT "
        "VALUE is an entity type.\n"
        "- For those, add conceptualSchema.entities entries and "
        "physicalMapping.entities entries mapping to LABEL with "
        "typeField/typeValue.\n\n"
        f"PHYSICAL_SCHEMA_SNAPSHOT_JSON:\n{snapshot_json}\n"
    )


def _compute_confidence(errors: list[str], warnings: list[str]) -> float:
    if errors:
        return 0.0
    penalty = min(CONFIDENCE_MAX_PENALTY, CONFIDENCE_WARNING_PENALTY * len(warnings))
    return max(CONFIDENCE_FLOOR, CONFIDENCE_BASE - penalty)


def _api_key_from_env(provider: str) -> str | None:
    env_var = get_provider_env_var(provider)
    return os.environ.get(env_var) if env_var else None


class _AnalysisContext(NamedTuple):
    """Prepared context for LLM analysis workflow."""

    snapshot: dict[str, Any]
    fingerprint: str
    cache_storage_key: str
    provider: Any
    model: str
    remaining_ms: int
    system: str
    prompt: str
    max_repair_attempts: int


@dataclass(frozen=True)
class _ProvenanceStamp:
    run_id: str
    started_at: str


@dataclass
class AgenticSchemaAnalyzer:
    llm_provider: Literal["openai", "anthropic", "openrouter"] | str | None = None
    api_key: str | None = None
    model: str | None = None
    cache: AnalysisCache | dict[str, Any] | None = None
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD
    system_prompt: str | None = None
    prompt_version: str | None = None
    max_repair_attempts: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.cache, dict) or self.cache is None:
            self.cache = cache_from_config(self.cache if isinstance(self.cache, dict) else None)

    def _effective_system_prompt(self) -> str:
        return self.system_prompt if self.system_prompt else _default_system_prompt()

    def _repair_limit(self) -> int:
        return self.max_repair_attempts if self.max_repair_attempts is not None else MAX_REPAIR_ATTEMPTS

    def _stamp_metadata(
        self,
        meta: AnalysisMetadata,
        *,
        prov: _ProvenanceStamp,
        physical_fingerprint: str,
        cache_hit: bool,
    ) -> AnalysisMetadata:
        return meta.model_copy(
            update={
                "run_id": prov.run_id,
                "analysis_started_at": prov.started_at,
                "analysis_completed_at": now_iso(),
                "physical_schema_fingerprint": physical_fingerprint,
                "cache_hit": cache_hit,
                "prompt_version": self.prompt_version,
            }
        )

    def _prepare_analysis(
        self,
        db: StandardDatabase,
        *,
        prov: _ProvenanceStamp,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        sample_limit_per_collection: int = 0,
        include_samples_in_snapshot: bool = False,
        use_cache: bool = True,
        _snapshot: dict[str, Any] | None = None,
    ) -> AnalysisResult | _AnalysisContext:
        """Shared setup for sync and async analysis paths.

        Returns an ``AnalysisResult`` on cache hit or no-provider baseline,
        or an ``_AnalysisContext`` with prepared values for the LLM workflow.
        """
        started = time.time()

        snapshot = _snapshot or snapshot_physical_schema(
            db,
            sample_limit_per_collection=sample_limit_per_collection,
            include_samples_in_snapshot=include_samples_in_snapshot,
        )
        snapshot["generated_at"] = now_iso()
        fingerprint = fingerprint_physical_schema(snapshot, include_samples=False)

        api_key = self.api_key or (_api_key_from_env(self.llm_provider) if self.llm_provider else None)
        use_llm = bool(self.llm_provider and api_key)
        system_effective = self._effective_system_prompt()
        llm_segment = f"{self.prompt_version or ''}\x00{system_effective}" if use_llm else None
        cache_storage_key = analysis_cache_storage_key(fingerprint, llm_cache_segment=llm_segment)

        if use_cache and self.cache is not None:
            cached = self.cache.get(cache_storage_key)
            if cached:
                logger.info("Cache hit for key prefix %s", cache_storage_key[:16])
                parsed = AnalysisResult.model_validate(cached)
                stamped = self._stamp_metadata(
                    parsed.metadata,
                    prov=prov,
                    physical_fingerprint=fingerprint,
                    cache_hit=True,
                )
                return AnalysisResult(
                    conceptual_schema=parsed.conceptual_schema,
                    physical_mapping=parsed.physical_mapping,
                    metadata=stamped,
                )

        if not use_llm:
            logger.info("No LLM provider configured; falling back to baseline inference")
            doc_count = sum(1 for c in snapshot.get("collections", []) if c.get("type") == "document")
            edge_count = sum(1 for c in snapshot.get("collections", []) if c.get("type") == "edge")
            baseline = infer_baseline_from_snapshot(snapshot)
            meta = AnalysisMetadata(
                confidence=0.1,
                timestamp=now_iso(),
                analyzed_collection_counts={"documentCollections": doc_count, "edgeCollections": edge_count},
                detected_patterns=[],
                warnings=["LLM provider not configured; returning deterministic baseline inference"],
                assumptions=[],
                review_required=True,
                provider=str(self.llm_provider) if self.llm_provider else None,
                model=None,
                repair_attempts=0,
                used_baseline=True,
            )
            meta = self._stamp_metadata(meta, prov=prov, physical_fingerprint=fingerprint, cache_hit=False)
            result = AnalysisResult(
                conceptual_schema=ConceptualSchema.from_json(baseline.get("conceptualSchema", {})).to_json(),
                physical_mapping=PhysicalMapping.from_json(baseline.get("physicalMapping", {})).to_json(),
                metadata=meta,
            )
            if use_cache and self.cache is not None:
                self.cache.set(
                    cache_storage_key,
                    _strip_provenance_for_cache(result.model_dump()),
                    ttl_seconds=self.cache_ttl_seconds,
                )
            return result

        logger.info("Using LLM provider=%s", self.llm_provider)
        provider = create_provider(self.llm_provider, api_key=api_key)
        model = self.model or get_default_model(self.llm_provider)

        elapsed_ms = int((time.time() - started) * 1000)
        remaining = max(MIN_LLM_BUDGET_MS, timeout_ms - elapsed_ms)

        prompt = _build_prompt(snapshot)

        return _AnalysisContext(
            snapshot=snapshot,
            fingerprint=fingerprint,
            cache_storage_key=cache_storage_key,
            provider=provider,
            model=model,
            remaining_ms=remaining,
            system=system_effective,
            prompt=prompt,
            max_repair_attempts=self._repair_limit(),
        )

    def analyze_physical_schema(
        self,
        db: StandardDatabase,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        sample_limit_per_collection: int = 0,
        include_samples_in_snapshot: bool = False,
        use_cache: bool = True,
        _snapshot: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        prov = _ProvenanceStamp(run_id=str(uuid.uuid4()), started_at=now_iso())
        prep = self._prepare_analysis(
            db,
            prov=prov,
            timeout_ms=timeout_ms,
            sample_limit_per_collection=sample_limit_per_collection,
            include_samples_in_snapshot=include_samples_in_snapshot,
            use_cache=use_cache,
            _snapshot=_snapshot,
        )
        if isinstance(prep, AnalysisResult):
            return prep

        errors: list[str] = []
        warnings: list[str] = []
        try:
            wf = run_generate_validate_repair(
                provider=prep.provider,
                model=prep.model,
                system=prep.system,
                prompt=prep.prompt,
                timeout_ms=prep.remaining_ms,
                max_repair_attempts=prep.max_repair_attempts,
            )
            data = wf.data
            repair_attempts = wf.repair_attempts
        except SchemaAnalyzerError as e:
            logger.warning("LLM workflow failed, falling back to baseline: %s", e)
            baseline = infer_baseline_from_snapshot(prep.snapshot)
            data = {
                "conceptualSchema": baseline.get("conceptualSchema", {}),
                "physicalMapping": baseline.get("physicalMapping", {}),
                "metadata": {"warnings": [str(e)]},
            }
            warnings.append("LLM workflow failed; returning deterministic baseline inference")
            errors.append(str(e))
            repair_attempts = 0

        return self._build_result(
            snapshot=prep.snapshot,
            data=data,
            model=prep.model,
            errors=errors,
            warnings=warnings,
            repair_attempts=repair_attempts,
            fingerprint=prep.fingerprint,
            cache_storage_key=prep.cache_storage_key,
            use_cache=use_cache,
            prov=prov,
        )

    async def analyze_physical_schema_async(
        self,
        db: StandardDatabase,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        sample_limit_per_collection: int = 0,
        include_samples_in_snapshot: bool = False,
        use_cache: bool = True,
        _snapshot: dict[str, Any] | None = None,
    ) -> AnalysisResult:
        """Async version of analyze_physical_schema. Requires provider with agenerate()."""
        prov = _ProvenanceStamp(run_id=str(uuid.uuid4()), started_at=now_iso())
        prep = self._prepare_analysis(
            db,
            prov=prov,
            timeout_ms=timeout_ms,
            sample_limit_per_collection=sample_limit_per_collection,
            include_samples_in_snapshot=include_samples_in_snapshot,
            use_cache=use_cache,
            _snapshot=_snapshot,
        )
        if isinstance(prep, AnalysisResult):
            return prep

        errors: list[str] = []
        warnings: list[str] = []
        try:
            wf = await async_generate_validate_repair(
                provider=prep.provider,
                model=prep.model,
                system=prep.system,
                prompt=prep.prompt,
                timeout_ms=prep.remaining_ms,
                max_repair_attempts=prep.max_repair_attempts,
            )
            data = wf.data
            repair_attempts = wf.repair_attempts
        except SchemaAnalyzerError as e:
            logger.warning("Async LLM workflow failed, falling back to baseline: %s", e)
            baseline = infer_baseline_from_snapshot(prep.snapshot)
            data = {
                "conceptualSchema": baseline.get("conceptualSchema", {}),
                "physicalMapping": baseline.get("physicalMapping", {}),
                "metadata": {"warnings": [str(e)]},
            }
            warnings.append("LLM workflow failed; returning deterministic baseline inference")
            errors.append(str(e))
            repair_attempts = 0

        return self._build_result(
            snapshot=prep.snapshot,
            data=data,
            model=prep.model,
            errors=errors,
            warnings=warnings,
            repair_attempts=repair_attempts,
            fingerprint=prep.fingerprint,
            cache_storage_key=prep.cache_storage_key,
            use_cache=use_cache,
            prov=prov,
        )

    def _build_result(
        self,
        *,
        snapshot: dict[str, Any],
        data: dict[str, Any],
        model: str,
        errors: list[str],
        warnings: list[str],
        repair_attempts: int,
        fingerprint: str,
        cache_storage_key: str,
        use_cache: bool,
        prov: _ProvenanceStamp,
    ) -> AnalysisResult:
        doc_count = sum(1 for c in snapshot.get("collections", []) if c.get("type") == "document")
        edge_count = sum(1 for c in snapshot.get("collections", []) if c.get("type") == "edge")

        if errors:
            confidence = 0.0
        else:
            confidence = (
                float(data.get("metadata", {}).get("confidence"))
                if isinstance(data.get("metadata"), dict)
                and isinstance(data.get("metadata", {}).get("confidence"), (int, float))
                else _compute_confidence(errors, warnings)
            )
        confidence = max(0.0, min(1.0, confidence))
        review_required = confidence < self.review_threshold or bool(errors)

        metadata = AnalysisMetadata(
            confidence=confidence,
            timestamp=str(data.get("metadata", {}).get("timestamp") or now_iso()),
            analyzed_collection_counts={"documentCollections": doc_count, "edgeCollections": edge_count},
            detected_patterns=list(data.get("metadata", {}).get("detectedPatterns") or []),
            warnings=list(data.get("metadata", {}).get("warnings") or []) + warnings + errors,
            assumptions=list(data.get("metadata", {}).get("assumptions") or []),
            review_required=review_required,
            provider=str(self.llm_provider).lower() if self.llm_provider else None,
            model=model,
            repair_attempts=int(repair_attempts),
            used_baseline=bool(errors),
        )
        metadata = self._stamp_metadata(metadata, prov=prov, physical_fingerprint=fingerprint, cache_hit=False)

        conceptual_schema = ConceptualSchema.from_json(
            data.get("conceptualSchema", {}) if isinstance(data.get("conceptualSchema"), dict) else {}
        ).to_json()
        physical_mapping = PhysicalMapping.from_json(
            data.get("physicalMapping", {}) if isinstance(data.get("physicalMapping"), dict) else {}
        ).to_json()

        result = AnalysisResult(
            conceptual_schema=conceptual_schema,
            physical_mapping=physical_mapping,
            metadata=metadata,
        )

        if use_cache and self.cache is not None:
            logger.debug("Caching result for cache key prefix %s", cache_storage_key[:16])
            self.cache.set(
                cache_storage_key,
                _strip_provenance_for_cache(result.model_dump()),
                ttl_seconds=self.cache_ttl_seconds,
            )

        logger.info(
            "Analysis complete: confidence=%.2f, review_required=%s, repair_attempts=%d",
            confidence,
            review_required,
            repair_attempts,
        )
        return result

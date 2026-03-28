from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

import os

from .cache import AnalysisCache, cache_from_config
from .conceptual import ConceptualSchema
from .baseline import infer_baseline_from_snapshot
from .errors import SchemaAnalyzerError
from .mapping import PhysicalMapping
from .snapshot import fingerprint_physical_schema, snapshot_physical_schema
from .types import AnalysisMetadata, AnalysisResult, now_iso
from .utils import extract_first_json_object
from .utils import stable_dumps
from .validation import validate_analysis_output
from .workflow import run_generate_validate_repair


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
        '    "entities": [{"name":"EntityType","labels":["EntityType"],"properties":[{"name":"prop"}]}],\n'
        '    "relationships": [{"type":"REL_TYPE","fromEntity":"EntityType","toEntity":"EntityType","properties":[{"name":"prop"}]}],\n'
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
        "- Prefer entity/relationship names that match collection names, type-field values, and edge collection names found in the snapshot.\n"
        "- Always include non-empty arrays for conceptualSchema.entities and conceptualSchema.relationships if any are inferable.\n\n"
        "Per-collection entity rule (CRITICAL):\n"
        "- If you see document collections that represent distinct entity types (i.e. NOT a single generic 'entities' collection with many type values), then EVERY document collection should become an entity type.\n"
        "- Use collection.inferred_entity_type as the entity name and create a physicalMapping.entities entry with style=COLLECTION and collectionName=<collection.name>.\n\n"
        "Generic edge collection rule (CRITICAL):\n"
        "- If an edge collection has sample_field_value_counts for a field like 'relation'/'relType'/'type', then EACH DISTINCT VALUE is a relationship type.\n"
        "- For those, add a conceptualSchema.relationships entry with type=<value> and add a physicalMapping.relationships entry mapping that type to GENERIC_WITH_TYPE on that edge collection and typeField=<field> typeValue=<value>.\n\n"
        "Generic entity collection rule:\n"
        "- If a document collection has sample_field_value_counts for a field like 'type'/'kind'/'entityType', then EACH DISTINCT VALUE is an entity type.\n"
        "- For those, add conceptualSchema.entities entries and physicalMapping.entities entries mapping to LABEL with typeField/typeValue.\n\n"
        f"PHYSICAL_SCHEMA_SNAPSHOT_JSON:\n{snapshot_json}\n"
    )


def _compute_confidence(errors: list[str], warnings: list[str]) -> float:
    if errors:
        return 0.0
    # Simple v0.1 heuristic: start high and subtract for warnings.
    base = 0.9
    penalty = min(0.6, 0.05 * len(warnings))
    return max(0.1, base - penalty)


def _provider_from_name(name: str, api_key: str):
    name = (name or "").lower()
    if name == "openai":
        from .providers.openai_provider import OpenAIProvider

        return OpenAIProvider(api_key=api_key)
    if name == "anthropic":
        from .providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=api_key)
    if name == "openrouter":
        from .providers.openrouter_provider import OpenRouterProvider

        return OpenRouterProvider(api_key=api_key)
    raise SchemaAnalyzerError(f"Unknown llm_provider: {name}", code="INVALID_ARGUMENT")


def _api_key_from_env(provider: str) -> str | None:
    p = (provider or "").lower()
    if p == "openai":
        return os.environ.get("OPENAI_API_KEY")
    if p == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY")
    if p == "openrouter":
        return os.environ.get("OPENROUTER_API_KEY")
    return None


@dataclass
class AgenticSchemaAnalyzer:
    llm_provider: Literal["openai", "anthropic", "openrouter"] | str | None = None
    api_key: str | None = None
    model: str | None = None
    cache: AnalysisCache | dict[str, Any] | None = None
    cache_ttl_seconds: int = 24 * 60 * 60
    review_threshold: float = 0.6

    def __post_init__(self) -> None:
        if isinstance(self.cache, dict) or self.cache is None:
            self.cache = cache_from_config(self.cache if isinstance(self.cache, dict) else None)

    def analyze_physical_schema(
        self,
        db,
        *,
        timeout_ms: int = 60_000,
        sample_limit_per_collection: int = 0,
        include_samples_in_snapshot: bool = False,
        use_cache: bool = True,
    ) -> AnalysisResult:
        started = time.time()

        snapshot = snapshot_physical_schema(
            db,
            sample_limit_per_collection=sample_limit_per_collection,
            include_samples_in_snapshot=include_samples_in_snapshot,
        )
        snapshot["generated_at"] = now_iso()
        fingerprint = fingerprint_physical_schema(snapshot, include_samples=False)

        if use_cache and self.cache is not None:
            cached = self.cache.get(fingerprint)
            if cached:
                # best-effort TTL enforcement: if cached payload has timestamp + ttl marker, respect it
                # otherwise assume valid.
                return AnalysisResult.model_validate(cached)

        # If no provider or no key, degrade gracefully with a deterministic minimal output.
        api_key = self.api_key or (_api_key_from_env(self.llm_provider) if self.llm_provider else None)
        if not self.llm_provider or not api_key:
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
            result = AnalysisResult(
                conceptual_schema=ConceptualSchema.from_json(baseline.get("conceptualSchema", {})).to_json(),
                physical_mapping=PhysicalMapping.from_json(baseline.get("physicalMapping", {})).to_json(),
                metadata=meta,
            )
            if use_cache and self.cache is not None:
                self.cache.set(fingerprint, result.model_dump(), ttl_seconds=self.cache_ttl_seconds)
            return result

        provider = _provider_from_name(self.llm_provider, api_key)
        prov = str(self.llm_provider).lower()
        if self.model:
            model = self.model
        elif prov == "openai":
            model = "gpt-4o-mini"
        elif prov == "openrouter":
            model = "openai/gpt-4o-mini"
        else:
            model = "claude-3-5-sonnet-latest"

        elapsed_ms = int((time.time() - started) * 1000)
        remaining = max(1_000, timeout_ms - elapsed_ms)

        system = _default_system_prompt()
        prompt = _build_prompt(snapshot)
        errors: list[str] = []
        warnings: list[str] = []
        try:
            wf = run_generate_validate_repair(
                provider=provider,
                model=model,
                system=system,
                prompt=prompt,
                timeout_ms=remaining,
                max_repair_attempts=2,
            )
            data = wf.data
            repair_attempts = wf.repair_attempts
        except SchemaAnalyzerError as e:
            # Hard failure: fall back to baseline inference but preserve diagnostics in warnings.
            baseline = infer_baseline_from_snapshot(snapshot)
            data = {
                "conceptualSchema": baseline.get("conceptualSchema", {}),
                "physicalMapping": baseline.get("physicalMapping", {}),
                "metadata": {"warnings": [str(e)]},
            }
            warnings.append("LLM workflow failed; returning deterministic baseline inference")
            errors.append(str(e))
            repair_attempts = 0

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

        conceptual_schema = ConceptualSchema.from_json(data.get("conceptualSchema", {}) if isinstance(data.get("conceptualSchema"), dict) else {}).to_json()
        physical_mapping = PhysicalMapping.from_json(data.get("physicalMapping", {}) if isinstance(data.get("physicalMapping"), dict) else {}).to_json()

        result = AnalysisResult(
            conceptual_schema=conceptual_schema,
            physical_mapping=physical_mapping,
            metadata=metadata,
        )

        if use_cache and self.cache is not None:
            self.cache.set(fingerprint, result.model_dump(), ttl_seconds=self.cache_ttl_seconds)

        return result


"""
Business-domain detection from a physical schema snapshot.

Extracts "signal tokens" (collection names, type values, field names, graph
names) and scores them against the domain specs in ``domains/`` plus a small
set of built-in keyword vocabularies for common domains that may not have a
spec file.

Returns the best-matching domain name + description + a confidence score,
which can be injected into the LLM prompt so the model has strong semantic
priors for naming entities and relationships.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DOMAINS_DIR = Path(__file__).resolve().parents[1] / "domains"

# ── Built-in keyword vocabularies for domains without spec files ────────
# Each maps a domain label to a set of lowercase keywords that, when found
# in the snapshot signals, hint at that domain.

_BUILTIN_DOMAINS: dict[str, dict[str, Any]] = {
    "entertainment_movies": {
        "description": "Entertainment / movies domain: movies, actors, directors, reviews.",
        "keywords": {
            "movie",
            "movies",
            "film",
            "actor",
            "actress",
            "director",
            "acted_in",
            "directed",
            "produced",
            "reviewed",
            "wrote",
            "title",
            "released",
            "tagline",
            "rating",
            "roles",
        },
    },
    "social_network": {
        "description": "Social network: users, posts, comments, follows, likes.",
        "keywords": {
            "user",
            "users",
            "post",
            "posts",
            "comment",
            "comments",
            "follows",
            "likes",
            "friend",
            "friends",
            "follower",
            "profile",
            "feed",
            "timeline",
            "message",
            "messages",
        },
    },
    "ecommerce": {
        "description": "E-commerce: customers, orders, products, categories, suppliers.",
        "keywords": {
            "customer",
            "customers",
            "order",
            "orders",
            "product",
            "products",
            "category",
            "categories",
            "supplier",
            "suppliers",
            "cart",
            "purchased",
            "ordered",
            "shipped",
            "price",
            "quantity",
            "sku",
            "inventory",
            "catalog",
        },
    },
    "graphrag": {
        "description": "Graph RAG (retrieval-augmented generation): documents, chunks, entities, mentions.",
        "keywords": {
            "chunk",
            "chunks",
            "document",
            "documents",
            "entity",
            "entities",
            "mention",
            "mentions",
            "embedding",
            "vector",
            "graphrag",
            "community",
            "communities",
            "source",
            "text_unit",
        },
    },
}


@dataclass
class DomainHint:
    """Result of domain detection."""

    domain: str
    description: str
    confidence: float
    matched_signals: list[str] = field(default_factory=list)

    # If the domain came from a spec file, its full entity/relationship vocabulary
    # is available for prompt enrichment.
    spec: dict[str, Any] | None = None

    def prompt_context(self) -> str:
        """Format domain hint as LLM prompt context."""
        lines = [f"Detected business domain: {self.domain}"]
        lines.append(f"Description: {self.description}")
        if self.spec:
            ent_names = [e["name"] for e in self.spec.get("entities", []) if isinstance(e, dict)]
            rel_types = [r["type"] for r in self.spec.get("relationships", []) if isinstance(r, dict)]
            if ent_names:
                lines.append(f"Typical entity types: {', '.join(ent_names)}")
            if rel_types:
                lines.append(f"Typical relationship types: {', '.join(rel_types)}")
        return "\n".join(lines)


def _extract_signal_tokens(snapshot: dict[str, Any]) -> set[str]:
    """
    Extract a set of lowercase signal tokens from the snapshot.
    These tokens represent the "vocabulary" of the database schema.
    """
    tokens: set[str] = set()
    for col in snapshot.get("collections") or []:
        if not isinstance(col, dict):
            continue

        name = col.get("name", "")
        if isinstance(name, str) and name:
            tokens.add(name.lower())
            for part in name.lower().replace("-", "_").split("_"):
                if part:
                    tokens.add(part)

        for val in _iter_type_values_from_col(col):
            tokens.add(val.lower())
            for part in val.lower().replace("-", "_").split("_"):
                if part:
                    tokens.add(part)

        for f in _iter_field_names_from_col(col):
            tokens.add(f.lower())

    for g in snapshot.get("graphs_detailed") or []:
        if isinstance(g, dict):
            gname = g.get("name", "")
            if isinstance(gname, str) and gname:
                tokens.add(gname.lower())
                for part in gname.lower().replace("-", "_").split("_"):
                    if part:
                        tokens.add(part)

    for g in snapshot.get("graphs") or []:
        if isinstance(g, dict):
            gname = g.get("name", "")
            if isinstance(gname, str) and gname:
                tokens.add(gname.lower())
                for part in gname.lower().replace("-", "_").split("_"):
                    if part:
                        tokens.add(part)

    return tokens


def _iter_type_values_from_col(col: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field_items in (col.get("sample_field_value_counts") or {}).values():
        if not isinstance(field_items, list):
            continue
        for item in field_items:
            if isinstance(item, dict) and "value" in item:
                v = item["value"]
                if isinstance(v, str) and v.strip():
                    values.append(v.strip())
    return values


def _iter_field_names_from_col(col: dict[str, Any]) -> list[str]:
    observed = col.get("observed_fields") or {}
    if isinstance(observed.get("fields"), list):
        return [f for f in observed["fields"] if isinstance(f, str)]
    if isinstance(observed.get("by_type"), dict):
        names: list[str] = []
        for fields in observed["by_type"].values():
            if isinstance(fields, list):
                names.extend(f for f in fields if isinstance(f, str))
        return names
    return []


def _build_spec_keywords(spec: dict[str, Any]) -> set[str]:
    """Build a keyword set from a domain spec."""
    keywords: set[str] = set()
    domain_name = spec.get("domain", "")
    if isinstance(domain_name, str):
        for part in domain_name.lower().replace("-", "_").split("_"):
            if part:
                keywords.add(part)

    for ent in spec.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        name = ent.get("name", "")
        if isinstance(name, str):
            keywords.add(name.lower())
        for prop in ent.get("properties") or []:
            if isinstance(prop, str):
                keywords.add(prop.lower())

    for rel in spec.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        rtype = rel.get("type", "")
        if isinstance(rtype, str):
            keywords.add(rtype.lower())
            for part in rtype.lower().split("_"):
                if part:
                    keywords.add(part)
        for prop in rel.get("properties") or []:
            if isinstance(prop, str):
                keywords.add(prop.lower())

    return keywords


def _score(signal_tokens: set[str], keywords: set[str]) -> tuple[float, list[str]]:
    """Jaccard-like overlap score between signal tokens and domain keywords."""
    if not keywords:
        return 0.0, []
    matched = signal_tokens & keywords
    if not matched:
        return 0.0, []
    score = len(matched) / len(keywords)
    return score, sorted(matched)


def _load_domain_specs() -> list[dict[str, Any]]:
    """Load all domain.json files from the domains/ directory."""
    specs: list[dict[str, Any]] = []
    if not _DOMAINS_DIR.exists():
        return specs
    import json

    for p in sorted(_DOMAINS_DIR.iterdir()):
        spec_path = p / "domain.json" if p.is_dir() else None
        if spec_path and spec_path.exists():
            try:
                data = json.loads(spec_path.read_text("utf-8"))
                if isinstance(data, dict):
                    specs.append(data)
            except Exception:
                continue
    return specs


def detect_domain(snapshot: dict[str, Any], *, min_confidence: float = 0.15) -> DomainHint | None:
    """
    Detect the business domain of a database from its physical schema snapshot.

    Scores snapshot signals against:
    1. Domain specs from the ``domains/`` directory (entity names, relationship
       types, property names)
    2. Built-in keyword vocabularies for common domains without spec files

    Returns the best match above ``min_confidence``, or ``None`` if no domain
    is detected with sufficient confidence.
    """
    tokens = _extract_signal_tokens(snapshot)
    if not tokens:
        return None

    best: DomainHint | None = None
    best_score = 0.0

    for spec in _load_domain_specs():
        keywords = _build_spec_keywords(spec)
        score, matched = _score(tokens, keywords)
        if score > best_score:
            best_score = score
            best = DomainHint(
                domain=spec.get("domain", "unknown"),
                description=spec.get("description", ""),
                confidence=round(min(score * 2, 1.0), 2),
                matched_signals=matched,
                spec=spec,
            )

    for domain_key, info in _BUILTIN_DOMAINS.items():
        keywords = info["keywords"]
        score, matched = _score(tokens, keywords)
        if score > best_score:
            best_score = score
            best = DomainHint(
                domain=domain_key,
                description=info["description"],
                confidence=round(min(score * 2, 1.0), 2),
                matched_signals=matched,
                spec=None,
            )

    if best and best.confidence >= min_confidence:
        logger.info("Detected domain=%s confidence=%.2f matched=%s", best.domain, best.confidence, best.matched_signals)
        return best

    return None

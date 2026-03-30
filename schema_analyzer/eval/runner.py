from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..analyzer import AgenticSchemaAnalyzer
from .domain_loader import list_domains, load_domain_spec
from .generator import PhysicalVariant, materialize_domain_variant
from .scoring import score_against_domain, score_domain_range, score_mapping_style

logger = logging.getLogger(__name__)

DEFAULT_VARIANTS = [
    PhysicalVariant(name="collection_dedicated", entity_style="COLLECTION", rel_style="DEDICATED_COLLECTION"),
    PhysicalVariant(name="generic_generic", entity_style="GENERIC_WITH_TYPE", rel_style="GENERIC_WITH_TYPE"),
]


@dataclass
class EvalRunResult:
    domain: str
    variant: str
    provider: str | None
    model: str | None
    confidence: float
    review_required: bool
    score: dict[str, Any]
    domain_range: dict[str, Any]
    mapping_style: dict[str, Any]


def run_eval(
    db,
    *,
    analyzer: AgenticSchemaAnalyzer,
    domains: list[str] | None = None,
    variants: list[PhysicalVariant] | None = None,
    sample_limit: int = 3,
    timeout_ms: int = 60_000,
    scale: int = 5,
) -> list[EvalRunResult]:
    """Run evaluation across domain packs and physical variants."""
    variants = variants or DEFAULT_VARIANTS
    domain_names = domains or list_domains()

    if not domain_names:
        logger.warning("No domains found")
        return []

    results: list[EvalRunResult] = []

    for variant in variants:
        for domain_name in domain_names:
            spec = load_domain_spec(domain_name)
            logger.info("Evaluating domain=%s variant=%s", domain_name, variant.name)

            materialize_domain_variant(db, spec, variant, seed=1, scale=scale, create_graph=True)

            analysis = analyzer.analyze_physical_schema(
                db,
                sample_limit_per_collection=sample_limit,
                timeout_ms=timeout_ms,
            )

            score = score_against_domain(spec, analysis.conceptual_schema)
            dr = score_domain_range(spec, analysis.conceptual_schema)
            ms = score_mapping_style(spec, analysis.physical_mapping, variant)

            result = EvalRunResult(
                domain=domain_name,
                variant=variant.name,
                provider=analysis.metadata.provider,
                model=analysis.metadata.model,
                confidence=analysis.metadata.confidence,
                review_required=analysis.metadata.review_required,
                score=score,
                domain_range=dr,
                mapping_style=ms,
            )
            results.append(result)

            logger.info(
                "%s / %s: ent_f1=%.2f rel_f1=%.2f dr_f1=%.2f map_rel_acc=%.2f conf=%.2f",
                domain_name, variant.name,
                score["entities"]["f1"],
                score["relationships"]["f1"],
                dr["f1"],
                ms["relationships"]["accuracy"],
                analysis.metadata.confidence,
            )

    return results


def format_eval_table(results: list[EvalRunResult]) -> str:
    """Format eval results as a human-readable table."""
    lines = [
        f"{'Domain':28} {'Variant':22} {'Ent F1':>7} {'Rel F1':>7} {'DR F1':>7} {'Map Acc':>8} {'Conf':>6}",
        "-" * 90,
    ]
    for r in results:
        lines.append(
            f"{r.domain:28} {r.variant:22} "
            f"{r.score['entities']['f1']:7.2f} "
            f"{r.score['relationships']['f1']:7.2f} "
            f"{r.domain_range['f1']:7.2f} "
            f"{r.mapping_style['relationships']['accuracy']:8.2f} "
            f"{r.confidence:6.2f}"
        )
    return "\n".join(lines)


def save_eval_report(results: list[EvalRunResult], path: str | Path) -> None:
    """Save eval results as a JSON report."""
    data = [
        {
            "domain": r.domain,
            "variant": r.variant,
            "provider": r.provider,
            "model": r.model,
            "confidence": r.confidence,
            "review_required": r.review_required,
            "score": r.score,
            "domain_range": r.domain_range,
            "mapping_style": r.mapping_style,
        }
        for r in results
    ]
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")


def compare_reports(current: str | Path, baseline: str | Path) -> str:
    """Compare two JSON eval reports and return a diff summary."""
    cur = json.loads(Path(current).read_text("utf-8"))
    base = json.loads(Path(baseline).read_text("utf-8"))

    base_index: dict[str, dict[str, Any]] = {}
    for entry in base:
        key = f"{entry['domain']}|{entry['variant']}"
        base_index[key] = entry

    lines = [f"{'Domain':28} {'Variant':22} {'Metric':>10} {'Baseline':>9} {'Current':>9} {'Delta':>8}"]
    lines.append("-" * 90)

    for entry in cur:
        key = f"{entry['domain']}|{entry['variant']}"
        prev = base_index.get(key)
        if not prev:
            lines.append(f"{entry['domain']:28} {entry['variant']:22} (new — no baseline)")
            continue

        metrics = [
            ("ent_f1", entry["score"]["entities"]["f1"], prev["score"]["entities"]["f1"]),
            ("rel_f1", entry["score"]["relationships"]["f1"], prev["score"]["relationships"]["f1"]),
            ("dr_f1", entry["domain_range"]["f1"], prev["domain_range"]["f1"]),
            ("map_acc", entry["mapping_style"]["relationships"]["accuracy"], prev["mapping_style"]["relationships"]["accuracy"]),
            ("conf", entry["confidence"], prev["confidence"]),
        ]

        for name, cur_val, base_val in metrics:
            delta = cur_val - base_val
            marker = "+" if delta > 0.005 else ("-" if delta < -0.005 else " ")
            lines.append(
                f"{entry['domain']:28} {entry['variant']:22} {name:>10} {base_val:9.3f} {cur_val:9.3f} {marker}{abs(delta):7.3f}"
            )

    return "\n".join(lines)

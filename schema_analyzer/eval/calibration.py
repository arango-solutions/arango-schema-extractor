"""Confidence calibration from eval feedback (PRD §3.12.3 / §6.5).

The analyzer reports ``metadata.confidence`` as a self-assessed scalar, and the
eval harness measures *realized* quality (entity / relationship / domain-range
F1 and mapping-style accuracy) against domain-pack ground truth. This module
pairs the two so we can answer:

* **Is confidence calibrated?** — a reliability curve (binned predicted
  confidence vs observed quality) plus single-number summaries: Expected
  Calibration Error (ECE), Maximum Calibration Error (MCE), and the Brier
  score. A positive ``gap`` means the analyzer is *overconfident*.
* **Where should the review gate sit?** — a ``recommended_review_threshold``
  derived by maximizing Youden's J (true-positive minus false-positive rate)
  of the gate ``review_required = confidence < threshold`` against a binary
  "good run" label (observed quality ≥ ``quality_target``).

Everything here is pure and DB-free: it operates on report-entry dicts (the
shape :func:`schema_analyzer.eval.runner.save_eval_report` persists), so it is
unit-testable without a live ArangoDB and runs identically on freshly computed
results or a saved report.

**Inputs / formula / failure modes** (per the PRD requirement that any new
composite score document these):

* *Observed quality* of a run = the unweighted mean of the four available
  metrics (entity F1, relationship F1, domain-range F1, mapping-style relation
  accuracy). Missing metrics are skipped; a run with no usable metric is
  dropped.
* *ECE* = Σ_b (n_b / N) · |mean_confidence_b − mean_quality_b| over non-empty
  equal-width bins. *MCE* = max_b of the same per-bin gap. *Brier* =
  mean((confidence − quality)²) over all runs.
* *Failure modes*: with **no usable runs** the result carries
  ``status="empty"`` and null metrics. When **every** run is good (or none
  are), the gate cannot discriminate, so the threshold recommendation falls
  back (accept-all / flag-all respectively) and ``threshold_note`` explains it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..defaults import (
    DEFAULT_CALIBRATION_BINS,
    DEFAULT_CALIBRATION_QUALITY_TARGET,
    DEFAULT_REVIEW_THRESHOLD,
)

# Metric accessors over a report-entry dict, each returning a value in [0, 1]
# or None when absent. Kept explicit (rather than a deep walk) so the composite
# definition is auditable.
_METRIC_ACCESSORS: tuple[tuple[str, Callable[[dict[str, Any]], float | None]], ...] = (
    ("entity_f1", lambda e: _dig(e, "score", "entities", "f1")),
    ("relationship_f1", lambda e: _dig(e, "score", "relationships", "f1")),
    ("domain_range_f1", lambda e: _dig(e, "domain_range", "f1")),
    ("mapping_style_accuracy", lambda e: _dig(e, "mapping_style", "relationships", "accuracy")),
)


def _dig(entry: Any, *keys: str) -> float | None:
    """Return a nested numeric value, or None if any hop is missing/non-numeric."""
    cur: Any = entry
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    if isinstance(cur, bool) or not isinstance(cur, (int, float)):
        return None
    return float(cur)


def observed_quality(entry: dict[str, Any]) -> float | None:
    """Composite observed quality for one run: mean of the available metrics.

    Returns None when the entry carries no usable metric (so the run can be
    dropped from calibration rather than skewing it with a fabricated zero).
    """
    present: list[float] = [v for _, fn in _METRIC_ACCESSORS if (v := fn(entry)) is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _confidence(entry: dict[str, Any]) -> float | None:
    val = entry.get("confidence")
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return float(val)


def _bin_index(conf: float, n_bins: int) -> int:
    # Equal-width bins over [0, 1]; clamp so conf == 1.0 lands in the last bin.
    idx = int(conf * n_bins)
    if idx >= n_bins:
        idx = n_bins - 1
    if idx < 0:
        idx = 0
    return idx


def _recommend_threshold(
    pairs: list[tuple[float, float]],
    *,
    quality_target: float,
) -> tuple[float | None, str]:
    """Pick the review threshold maximizing Youden's J over the gate.

    Gate semantics: a run is *accepted* (not flagged for review) when
    ``confidence >= threshold``. We want accepted runs to be good
    (quality >= quality_target) and flagged runs to be bad. Returns
    ``(threshold, note)``; threshold is None only when there are no pairs.
    """
    if not pairs:
        return None, "no runs"

    labels = [(conf, q >= quality_target) for conf, q in pairs]
    n_good = sum(1 for _, good in labels if good)
    n_bad = len(labels) - n_good

    if n_good == 0:
        # Nothing meets the bar — flag everything (threshold above max conf).
        hi = max(conf for conf, _ in labels)
        return round(min(1.0, hi + 0.01), 4), "no runs met quality_target; gate flags all runs"
    if n_bad == 0:
        # Everything meets the bar — the gate is not discriminative; accept all.
        lo = min(conf for conf, _ in labels)
        return round(max(0.0, lo), 4), "all runs met quality_target; gate not discriminative"

    # Candidate thresholds: each observed confidence (accept conf >= t).
    candidates = sorted({conf for conf, _ in labels})
    best_t = candidates[0]
    best_j = -2.0
    for t in candidates:
        tp = sum(1 for conf, good in labels if good and conf >= t)
        fp = sum(1 for conf, good in labels if not good and conf >= t)
        tpr = tp / n_good
        fpr = fp / n_bad
        j = tpr - fpr
        # Prefer the higher threshold on ties (more conservative review gate).
        if j > best_j or (j == best_j and t > best_t):
            best_j = j
            best_t = t
    return round(best_t, 4), f"maximizes Youden's J ({best_j:.3f}) vs quality_target={quality_target}"


def compute_calibration(
    entries: list[dict[str, Any]],
    *,
    n_bins: int = DEFAULT_CALIBRATION_BINS,
    quality_target: float = DEFAULT_CALIBRATION_QUALITY_TARGET,
    current_review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> dict[str, Any]:
    """Compute the calibration summary for a list of eval report entries.

    See the module docstring for the definitions of every emitted field. The
    return value is JSON-serializable and deterministic.
    """
    pairs: list[tuple[float, float]] = []
    for entry in entries:
        conf = _confidence(entry)
        qual = observed_quality(entry)
        if conf is None or qual is None:
            continue
        pairs.append((conf, qual))

    if not pairs:
        return {
            "status": "empty",
            "n": 0,
            "n_bins": n_bins,
            "quality_target": quality_target,
            "current_review_threshold": current_review_threshold,
            "mean_confidence": None,
            "mean_quality": None,
            "gap": None,
            "ece": None,
            "mce": None,
            "brier": None,
            "recommended_review_threshold": None,
            "threshold_note": "no usable runs (missing confidence or metrics)",
            "bins": [],
        }

    n = len(pairs)
    mean_conf = sum(c for c, _ in pairs) / n
    mean_qual = sum(q for _, q in pairs) / n
    brier = sum((c - q) ** 2 for c, q in pairs) / n

    # Reliability bins.
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for conf, qual in pairs:
        buckets[_bin_index(conf, n_bins)].append((conf, qual))

    bins: list[dict[str, Any]] = []
    ece = 0.0
    mce = 0.0
    for i, bucket in enumerate(buckets):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        if not bucket:
            bins.append(
                {
                    "lo": round(lo, 4),
                    "hi": round(hi, 4),
                    "count": 0,
                    "mean_confidence": None,
                    "mean_quality": None,
                    "gap": None,
                }
            )
            continue
        bc = sum(c for c, _ in bucket) / len(bucket)
        bq = sum(q for _, q in bucket) / len(bucket)
        gap = bc - bq
        ece += (len(bucket) / n) * abs(gap)
        mce = max(mce, abs(gap))
        bins.append(
            {
                "lo": round(lo, 4),
                "hi": round(hi, 4),
                "count": len(bucket),
                "mean_confidence": round(bc, 4),
                "mean_quality": round(bq, 4),
                "gap": round(gap, 4),
            }
        )

    rec_threshold, note = _recommend_threshold(pairs, quality_target=quality_target)

    return {
        "status": "ok",
        "n": n,
        "n_bins": n_bins,
        "quality_target": quality_target,
        "current_review_threshold": current_review_threshold,
        "mean_confidence": round(mean_conf, 4),
        "mean_quality": round(mean_qual, 4),
        # gap > 0 => overconfident (predicts higher than observed quality).
        "gap": round(mean_conf - mean_qual, 4),
        "ece": round(ece, 4),
        "mce": round(mce, 4),
        "brier": round(brier, 4),
        "recommended_review_threshold": rec_threshold,
        "threshold_note": note,
        "bins": bins,
    }


def format_calibration_report(cal: dict[str, Any]) -> str:
    """Render a calibration summary as a human-readable table."""
    if cal.get("status") != "ok":
        return f"Calibration: {cal.get('threshold_note', 'unavailable')} (n={cal.get('n', 0)})"

    over = "overconfident" if cal["gap"] > 0 else ("underconfident" if cal["gap"] < 0 else "calibrated")
    lines = [
        f"Calibration (n={cal['n']}, quality_target={cal['quality_target']})",
        f"  mean confidence : {cal['mean_confidence']:.3f}",
        f"  mean quality    : {cal['mean_quality']:.3f}",
        f"  gap             : {cal['gap']:+.3f}  ({over})",
        f"  ECE / MCE       : {cal['ece']:.3f} / {cal['mce']:.3f}",
        f"  Brier           : {cal['brier']:.3f}",
        f"  review threshold: current={cal['current_review_threshold']:.3f}  "
        f"recommended={cal['recommended_review_threshold']:.3f}",
        f"                    ({cal['threshold_note']})",
        "",
        f"  {'conf bin':>12} {'count':>6} {'mean conf':>10} {'mean qual':>10} {'gap':>8}",
        "  " + "-" * 50,
    ]
    for b in cal["bins"]:
        if not b["count"]:
            continue
        lines.append(
            f"  [{b['lo']:.2f},{b['hi']:.2f}) {b['count']:>6} "
            f"{b['mean_confidence']:>10.3f} {b['mean_quality']:>10.3f} {b['gap']:>+8.3f}"
        )
    return "\n".join(lines)

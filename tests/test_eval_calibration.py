"""Tests for confidence calibration from eval feedback (PRD §3.12.3 / §6.5)."""

from __future__ import annotations

import json

from schema_analyzer.eval import (
    calibration_from_results,
    compare_reports,
    compute_calibration,
    format_calibration_report,
    observed_quality,
    save_eval_report,
)
from schema_analyzer.eval.runner import EvalRunResult


def _entry(conf, ef1=None, rf1=None, drf1=None, macc=None):
    """Build a report-entry dict; metrics default to ``conf`` for perfect calibration."""
    ef1 = conf if ef1 is None else ef1
    rf1 = conf if rf1 is None else rf1
    drf1 = conf if drf1 is None else drf1
    macc = conf if macc is None else macc
    return {
        "confidence": conf,
        "score": {"entities": {"f1": ef1}, "relationships": {"f1": rf1}},
        "domain_range": {"f1": drf1},
        "mapping_style": {"relationships": {"accuracy": macc}},
    }


def _result(domain, conf, q):
    return EvalRunResult(
        domain=domain,
        variant="v",
        provider=None,
        model=None,
        confidence=conf,
        review_required=conf < 0.6,
        score={"entities": {"f1": q}, "relationships": {"f1": q}},
        domain_range={"f1": q},
        mapping_style={"relationships": {"accuracy": q}},
    )


# --- observed_quality ---------------------------------------------------------


def test_observed_quality_is_mean_of_metrics():
    q = observed_quality(_entry(0.0, ef1=0.2, rf1=0.4, drf1=0.6, macc=0.8))
    assert q == 0.5


def test_observed_quality_skips_missing_metrics():
    entry = {"confidence": 0.5, "score": {"entities": {"f1": 0.8}}}
    assert observed_quality(entry) == 0.8


def test_observed_quality_none_when_no_metrics():
    assert observed_quality({"confidence": 0.5}) is None


def test_observed_quality_ignores_bool_and_nonnumeric():
    entry = {"score": {"entities": {"f1": True}, "relationships": {"f1": "x"}}}
    assert observed_quality(entry) is None


# --- compute_calibration: summary stats --------------------------------------


def test_perfect_calibration_zero_error():
    cal = compute_calibration([_entry(0.2), _entry(0.5), _entry(0.9)])
    assert cal["status"] == "ok"
    assert cal["n"] == 3
    assert cal["gap"] == 0.0
    assert cal["ece"] == 0.0
    assert cal["brier"] == 0.0


def test_overconfident_positive_gap():
    # confidence 0.9 but quality 0.5 -> overconfident
    cal = compute_calibration([_entry(0.9, ef1=0.5, rf1=0.5, drf1=0.5, macc=0.5)])
    assert cal["gap"] > 0
    assert cal["mean_confidence"] == 0.9
    assert cal["mean_quality"] == 0.5
    assert cal["brier"] == round((0.9 - 0.5) ** 2, 4)


def test_underconfident_negative_gap():
    cal = compute_calibration([_entry(0.3, ef1=0.8, rf1=0.8, drf1=0.8, macc=0.8)])
    assert cal["gap"] < 0


def test_bins_partition_runs_and_last_bin_holds_one():
    cal = compute_calibration([_entry(0.05), _entry(1.0)], n_bins=10)
    counts = {(b["lo"], b["hi"]): b["count"] for b in cal["bins"]}
    assert counts[(0.0, 0.1)] == 1
    assert counts[(0.9, 1.0)] == 1  # conf == 1.0 clamped into last bin
    assert sum(b["count"] for b in cal["bins"]) == 2


# --- threshold recommendation ------------------------------------------------


def test_threshold_separates_good_from_bad():
    good = [_entry(0.9, ef1=0.9, rf1=0.9, drf1=0.9, macc=0.9) for _ in range(3)]
    bad = [_entry(0.3, ef1=0.2, rf1=0.2, drf1=0.2, macc=0.2) for _ in range(3)]
    cal = compute_calibration(good + bad, quality_target=0.7)
    assert cal["recommended_review_threshold"] == 0.9
    assert "Youden" in cal["threshold_note"]


def test_threshold_all_good_not_discriminative():
    cal = compute_calibration([_entry(0.8), _entry(0.9)], quality_target=0.7)
    assert "not discriminative" in cal["threshold_note"]
    assert cal["recommended_review_threshold"] <= 0.8


def test_threshold_all_bad_flags_all():
    cal = compute_calibration([_entry(0.4, ef1=0.1, rf1=0.1, drf1=0.1, macc=0.1)], quality_target=0.7)
    assert "flags all" in cal["threshold_note"]
    assert cal["recommended_review_threshold"] > 0.4


# --- failure modes -----------------------------------------------------------


def test_empty_input():
    cal = compute_calibration([])
    assert cal["status"] == "empty"
    assert cal["n"] == 0
    assert cal["recommended_review_threshold"] is None


def test_entries_without_metrics_are_dropped():
    cal = compute_calibration([{"confidence": 0.5}, {"confidence": 0.6}])
    assert cal["status"] == "empty"


# --- formatting --------------------------------------------------------------


def test_format_ok_report_mentions_direction():
    cal = compute_calibration([_entry(0.9, ef1=0.5, rf1=0.5, drf1=0.5, macc=0.5)])
    out = format_calibration_report(cal)
    assert "overconfident" in out
    assert "review threshold" in out


def test_format_empty_report():
    out = format_calibration_report(compute_calibration([]))
    assert "n=0" in out


# --- report round-trip + backward compat -------------------------------------


def test_save_report_embeds_calibration(tmp_path):
    results = [_result("d1", 0.9, 0.9), _result("d2", 0.3, 0.2)]
    path = tmp_path / "report.json"
    save_eval_report(results, path)
    data = json.loads(path.read_text())
    assert isinstance(data, dict)
    assert len(data["runs"]) == 2
    assert data["calibration"]["status"] == "ok"


def test_calibration_from_results_matches_save():
    results = [_result("d1", 0.9, 0.9), _result("d2", 0.3, 0.2)]
    cal = calibration_from_results(results)
    assert cal["n"] == 2


def test_compare_reports_reads_new_dict_shape(tmp_path):
    cur = tmp_path / "cur.json"
    base = tmp_path / "base.json"
    save_eval_report([_result("d1", 0.8, 0.8)], cur)
    save_eval_report([_result("d1", 0.6, 0.6)], base)
    out = compare_reports(cur, base)
    assert "d1" in out
    assert "ent_f1" in out


def test_compare_reports_shows_calibration_drift(tmp_path):
    cur = tmp_path / "cur.json"
    base = tmp_path / "base.json"
    # current is well-calibrated; baseline was overconfident
    save_eval_report([_result("d1", 0.9, 0.9), _result("d2", 0.3, 0.3)], cur)
    save_eval_report([_result("d1", 0.9, 0.4), _result("d2", 0.3, 0.1)], base)
    out = compare_reports(cur, base)
    assert "Calibration" in out
    assert "ece" in out
    assert "recommended_review_threshold" in out


def test_compare_reports_tolerates_legacy_list_baseline(tmp_path):
    # Old reports were a bare list; new code must still diff against them.
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
            [
                {
                    "domain": "d1",
                    "variant": "v",
                    "confidence": 0.5,
                    "score": {"entities": {"f1": 0.5}, "relationships": {"f1": 0.5}},
                    "domain_range": {"f1": 0.5},
                    "mapping_style": {"relationships": {"accuracy": 0.5}},
                }
            ]
        )
    )
    cur = tmp_path / "cur.json"
    save_eval_report([_result("d1", 0.8, 0.8)], cur)
    out = compare_reports(cur, legacy)
    assert "d1" in out

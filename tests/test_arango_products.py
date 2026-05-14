"""Tests for schema_analyzer.arango_products - first-party Arango
product detection (Autograph today)."""
from __future__ import annotations

import pytest

from schema_analyzer.arango_products import (
    ArangoProductReport,
    AutographProject,
    _match_suffix,
    detect_arango_products,
)


def _snap(collections=None, graphs=None) -> dict:
    return {
        "collections": [{"name": n} for n in (collections or [])],
        "graphs": [{"name": n} for n in (graphs or [])],
    }


# --- Suffix matching ---------------------------------------------------------


class TestMatchSuffix:
    def test_corpus_relations_wins_over_relations(self):
        prefix, suffix, kind, role = _match_suffix("foo_corpus_relations")
        assert prefix == "foo"
        assert suffix == "corpus_relations"
        assert kind == "edge"
        assert role == "corpus"

    def test_kg_named_graph(self):
        prefix, suffix, kind, role = _match_suffix("Project_kg")
        assert prefix == "Project"
        assert suffix == "kg"
        assert kind == "graph"
        assert role == "kg"

    def test_kg_relations_capital_r(self):
        prefix, suffix, kind, role = _match_suffix("Project_Relations")
        assert prefix == "Project"
        assert suffix == "Relations"
        assert kind == "edge"
        assert role == "kg"

    def test_unrelated_collection_returns_none(self):
        assert _match_suffix("Device") == (None, None, None, None)
        assert _match_suffix("Person_addresses") == (None, None, None, None)

    def test_empty_string(self):
        assert _match_suffix("") == (None, None, None, None)
        assert _match_suffix("nounderscore") == (None, None, None, None)


# --- Autograph detection ----------------------------------------------------


class TestDetectAutograph:
    def test_complete_project(self):
        snap = _snap(
            collections=[
                "P_Chunks", "P_Communities", "P_Documents", "P_Entities",
                "P_domains", "P_modules", "P_sources", "P_rags",
                "P_corpus_relations", "P_Relations", "P_similarities",
            ],
            graphs=["P_CorpusGraph", "P_kg"],
        )
        report = detect_arango_products(snap)
        assert len(report.autograph_projects) == 1
        proj = report.autograph_projects[0]
        assert proj.project_name == "P"
        assert proj.completeness == "complete"
        assert proj.corpus_graph == "P_CorpusGraph"
        assert proj.kg_graph == "P_kg"
        assert proj.confidence == 1.0
        assert proj.warnings == []

    def test_corpus_only_failed_run(self):
        snap = _snap(
            collections=[
                "X_domains", "X_modules", "X_sources", "X_rags",
                "X_corpus_relations", "X_similarities",
            ],
            graphs=["X_CorpusGraph"],
        )
        report = detect_arango_products(snap)
        assert len(report.autograph_projects) == 1
        proj = report.autograph_projects[0]
        assert proj.completeness == "corpus_only"
        assert proj.kg_graph is None
        assert any("INCOMPLETE_AUTOGRAPH_RUN" in w for w in proj.warnings)

    def test_kg_only_orphan(self):
        snap = _snap(
            collections=[
                "Y_Chunks", "Y_Communities", "Y_Documents", "Y_Entities",
                "Y_Relations",
            ],
            graphs=["Y_kg"],
        )
        report = detect_arango_products(snap)
        assert len(report.autograph_projects) == 1
        proj = report.autograph_projects[0]
        assert proj.completeness == "kg_only"
        assert proj.corpus_graph is None
        assert any("ORPHAN_AUTOGRAPH_KG" in w for w in proj.warnings)

    def test_strong_marker_gate_rejects_decoy(self):
        # A hand-built KG that happens to use one Autograph-shaped name
        # ("Documents") but lacks any of CorpusGraph / kg /
        # corpus_relations / rags MUST NOT be detected as Autograph.
        snap = _snap(collections=["MyApp_Documents"], graphs=["MyApp"])
        report = detect_arango_products(snap)
        assert report.is_empty

    def test_multi_project_split(self):
        snap = _snap(
            collections=[
                "A_domains", "A_modules", "A_sources", "A_rags",
                "A_corpus_relations",
                "B_Chunks", "B_Communities", "B_Documents", "B_Entities",
                "B_Relations", "B_domains", "B_modules", "B_sources",
                "B_rags", "B_corpus_relations",
            ],
            graphs=["A_CorpusGraph", "B_CorpusGraph", "B_kg"],
        )
        report = detect_arango_products(snap)
        names = {p.project_name for p in report.autograph_projects}
        assert names == {"A", "B"}
        by_name = {p.project_name: p for p in report.autograph_projects}
        assert by_name["A"].completeness == "corpus_only"
        assert by_name["B"].completeness == "complete"

    def test_implicit_link_only_when_both_endpoints_exist(self):
        # Corpus-only: no entity_type seed link possible.
        corpus_only = _snap(
            collections=[
                "P_domains", "P_modules", "P_sources", "P_rags",
                "P_corpus_relations",
            ],
            graphs=["P_CorpusGraph"],
        )
        proj = detect_arango_products(corpus_only).autograph_projects[0]
        assert proj.implicit_links == []

        # Complete: link emitted.
        complete = _snap(
            collections=[
                "P_domains", "P_modules", "P_sources", "P_rags",
                "P_Chunks", "P_Communities", "P_Documents", "P_Entities",
                "P_corpus_relations", "P_Relations",
            ],
            graphs=["P_CorpusGraph", "P_kg"],
        )
        proj = detect_arango_products(complete).autograph_projects[0]
        assert len(proj.implicit_links) == 1
        link = proj.implicit_links[0]
        assert link["from"] == "P_rags.entity_types"
        assert link["to"] == "P_Entities.entity_type"
        assert link["kind"] == "graphrag_entity_type_seed"

    def test_project_name_with_hyphens_and_dots(self):
        snap = _snap(
            collections=[
                "OpenRTB-API-Specification_domains",
                "OpenRTB-API-Specification_modules",
                "OpenRTB-API-Specification_sources",
                "OpenRTB-API-Specification_rags",
                "OpenRTB-API-Specification_corpus_relations",
            ],
            graphs=["OpenRTB-API-Specification_CorpusGraph"],
        )
        report = detect_arango_products(snap)
        assert len(report.autograph_projects) == 1
        assert (
            report.autograph_projects[0].project_name
            == "OpenRTB-API-Specification"
        )

    def test_empty_snapshot(self):
        assert detect_arango_products({}).is_empty
        assert detect_arango_products(_snap()).is_empty

    def test_graphs_detailed_key(self):
        # Snapshots in the wild use either ``graphs`` or ``graphs_detailed``.
        snap = {
            "collections": [
                {"name": n}
                for n in [
                    "P_domains", "P_modules", "P_sources", "P_rags",
                    "P_corpus_relations",
                ]
            ],
            "graphs_detailed": [{"name": "P_CorpusGraph"}],
        }
        report = detect_arango_products(snap)
        assert len(report.autograph_projects) == 1


class TestSerialization:
    def test_to_dict_round_trip(self):
        snap = _snap(
            collections=[
                "P_Chunks", "P_Communities", "P_Documents", "P_Entities",
                "P_rags", "P_corpus_relations", "P_Relations",
            ],
            graphs=["P_CorpusGraph", "P_kg"],
        )
        report = detect_arango_products(snap)
        as_dict = report.to_dict()
        assert as_dict["kind"] == "autograph"
        assert as_dict["version_hint"] == "graphrag"
        assert len(as_dict["projects"]) == 1
        proj = as_dict["projects"][0]
        for key in (
            "project_name", "completeness", "corpus_graph", "kg_graph",
            "corpus_collections", "kg_collections", "implicit_links",
            "warnings", "confidence",
        ):
            assert key in proj

import pytest

from schema_analyzer.tool import run_tool

from ..conftest import connect_root, env, skip_if_integration_not_enabled, wait_for_arango

pytestmark = pytest.mark.integration


def test_tool_snapshot_and_analyze_smoke():
    skip_if_integration_not_enabled()

    client, sys_db = connect_root()
    wait_for_arango(sys_db)

    base_db = env("ARANGO_DB", "schema_analyzer_it")
    db_name = f"{base_db}_tool_smoke"
    if sys_db.has_database(db_name):
        import contextlib

        with contextlib.suppress(Exception):
            sys_db.delete_database(db_name)
    sys_db.create_database(db_name)

    db = client.db(db_name, username=env("ARANGO_USER", "root"), password=env("ARANGO_PASS", "openSesame"))
    if not db.has_collection("users"):
        db.create_collection("users", edge=False)
    if not db.has_collection("follows"):
        db.create_collection("follows", edge=True)

    users = db.collection("users")
    follows = db.collection("follows")
    a = users.insert({"name": "Alice"})
    b = users.insert({"name": "Bob"})
    follows.insert({"_from": a["_id"], "_to": b["_id"], "relation": "FOLLOWS"})

    req_snapshot = {
        "contractVersion": "1",
        "operation": "snapshot",
        "connection": {
            "url": env("ARANGO_URL", "http://localhost:8529"),
            "database": db_name,
            "username": env("ARANGO_USER", "root"),
            "password": env("ARANGO_PASS", "openSesame"),
        },
        "analysisOptions": {"sampleLimitPerCollection": 1, "includeSamplesInSnapshot": False},
    }
    resp_snapshot = run_tool(req_snapshot)
    assert resp_snapshot["ok"] is True
    snap_cols = [c["name"] for c in resp_snapshot["result"]["snapshot"]["collections"]]
    assert "users" in snap_cols
    assert "follows" in snap_cols

    req_analyze = {
        "contractVersion": "1",
        "operation": "analyze",
        "connection": {
            "url": env("ARANGO_URL", "http://localhost:8529"),
            "database": db_name,
            "username": env("ARANGO_USER", "root"),
            "password": env("ARANGO_PASS", "openSesame"),
        },
        # No LLM config on purpose: baseline inference path should still succeed.
        "analysisOptions": {"sampleLimitPerCollection": 1, "includeSamplesInSnapshot": False, "timeoutMs": 60000},
    }
    resp_analyze = run_tool(req_analyze)
    assert resp_analyze["ok"] is True
    analysis = resp_analyze["result"]["analysis"]
    assert analysis["conceptualSchema"]["entities"], "should infer at least one entity"
    assert analysis["physicalMapping"]["entities"], "should produce entity mappings"


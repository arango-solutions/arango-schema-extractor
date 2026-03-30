import os
import sys
import time
from pathlib import Path

import pytest

# Ensure repository root is importable for the test suite.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Shared integration-test helpers
# ---------------------------------------------------------------------------

def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v is not None else default


def skip_if_integration_not_enabled():
    if env("RUN_INTEGRATION", "0") != "1":
        pytest.skip("integration tests disabled (set RUN_INTEGRATION=1)")


def connect_root():
    from arango import ArangoClient

    url = env("ARANGO_URL", "http://localhost:8529")
    user = env("ARANGO_USER", "root")
    pw = env("ARANGO_PASS", "openSesame")
    client = ArangoClient(hosts=url)
    return client, client.db("_system", username=user, password=pw)


def connect_db(db_name: str):
    from arango import ArangoClient

    url = env("ARANGO_URL", "http://localhost:8529")
    user = env("ARANGO_USER", "root")
    pw = env("ARANGO_PASS", "openSesame")
    client = ArangoClient(hosts=url)
    return client.db(db_name, username=user, password=pw)


def wait_for_arango(sys_db, timeout_s: float = 20.0):
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            sys_db.has_database("_system")
            return
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"ArangoDB not ready after {timeout_s}s: {last_err}")


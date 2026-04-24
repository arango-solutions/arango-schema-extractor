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


def ensure_fresh_database(sys_db, db_name: str) -> None:
    """Drop ``db_name`` if it exists and recreate it.

    Centralises the ``has_database / delete_database / create_database``
    dance every integration test used to duplicate. Safe to call
    repeatedly; ignores errors during the delete (e.g. not-found races).
    """
    import contextlib

    if sys_db.has_database(db_name):
        with contextlib.suppress(Exception):
            sys_db.delete_database(db_name)
    sys_db.create_database(db_name)


@pytest.fixture
def fresh_database(request):
    """Function-scoped fixture that yields a fresh ArangoDB handle.

    Usage:

        def test_something(fresh_database):
            db = fresh_database("my_test_db")
            ...

    The database is created fresh at request time and dropped at
    teardown. Safe to call multiple times in one test; each call yields
    an independent database. Skips automatically when integration tests
    are disabled (``RUN_INTEGRATION != "1"``).
    """
    import contextlib

    skip_if_integration_not_enabled()
    client, sys_db = connect_root()
    wait_for_arango(sys_db)
    created: list[str] = []

    def _make(db_name: str):
        ensure_fresh_database(sys_db, db_name)
        created.append(db_name)
        return client.db(
            db_name,
            username=env("ARANGO_USER", "root"),
            password=env("ARANGO_PASS", "openSesame"),
        )

    yield _make

    for name in created:
        with contextlib.suppress(Exception):
            sys_db.delete_database(name)

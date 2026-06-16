"""Typed helpers around python-arango's union return types.

python-arango annotates database and collection methods as returning
``Result[T] = T | AsyncJob[T] | BatchJob[T]`` so the same surface can drive
synchronous, async, and batch execution. This library only ever uses the
synchronous ``StandardDatabase`` path, where the concrete ``T`` is always
returned. Centralizing the ``cast`` here keeps call sites readable and lets
mypy stay strict everywhere else instead of drowning in union-attr noise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from arango.collection import StandardCollection
    from arango.cursor import Cursor
    from arango.database import StandardDatabase


def aql_execute(
    db: StandardDatabase,
    query: str,
    bind_vars: dict[str, Any] | None = None,
) -> Cursor:
    """Execute AQL synchronously and return the concrete cursor."""
    return cast("Cursor", db.aql.execute(query, bind_vars=bind_vars))


def collection_properties(col: StandardCollection) -> dict[str, Any]:
    return cast("dict[str, Any]", col.properties())


def collection_count(col: StandardCollection) -> int:
    return cast(int, col.count())


def collection_indexes(col: StandardCollection) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", col.indexes() or [])


def database_graphs(db: StandardDatabase) -> Any:
    return db.graphs()


def graph_properties(db: StandardDatabase, name: str) -> dict[str, Any]:
    return cast("dict[str, Any]", db.graph(name).properties())

from __future__ import annotations

import logging
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import TYPE_CHECKING, Any, Literal

from arango import ArangoClient

if TYPE_CHECKING:
    from arango.database import StandardDatabase

from .analyzer import AgenticSchemaAnalyzer
from .defaults import (
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_EXPORT_TARGET,
    DEFAULT_REVIEW_THRESHOLD,
    DEFAULT_TIMEOUT_MS,
    FALLBACK_LIBRARY_VERSION,
)
from .docs import generate_schema_docs
from .errors import SchemaAnalyzerError
from .exports import export_mapping
from .owl_export import export_conceptual_model_as_owl_turtle
from .snapshot import fingerprint_physical_schema, snapshot_physical_schema
from .tool_contract_v1 import CONTRACT_VERSION, validate_request_v1, validate_response_v1

logger = logging.getLogger(__name__)


Operation = Literal["analyze", "snapshot", "export", "docs", "owl"]


def _library_version() -> str:
    try:
        return pkg_version("arangodb-schema-analyzer")
    except PackageNotFoundError:
        return FALLBACK_LIBRARY_VERSION


def _env(name: str) -> str | None:
    return os.environ.get(name)


def _get_password(conn: dict[str, Any]) -> str | None:
    if "password" in conn and isinstance(conn.get("password"), str):
        return conn["password"]
    env_var = conn.get("passwordEnvVar")
    if isinstance(env_var, str) and env_var:
        return _env(env_var)
    return None


def _get_api_key(llm: dict[str, Any] | None) -> str | None:
    if not llm:
        return None
    if "apiKey" in llm and isinstance(llm.get("apiKey"), str):
        return llm["apiKey"]
    env_var = llm.get("apiKeyEnvVar")
    if isinstance(env_var, str) and env_var:
        return _env(env_var)
    return None


def _connect_db(conn: dict[str, Any]) -> StandardDatabase:
    url = conn.get("url")
    db_name = conn.get("database")
    username = conn.get("username") or "root"
    if not isinstance(url, str) or not url:
        raise SchemaAnalyzerError("connection.url is required", code="INVALID_ARGUMENT")
    if not isinstance(db_name, str) or not db_name:
        raise SchemaAnalyzerError("connection.database is required", code="INVALID_ARGUMENT")
    pw = _get_password(conn)
    if pw is None:
        raise SchemaAnalyzerError("Missing ArangoDB password (password or passwordEnvVar)", code="INVALID_ARGUMENT")
    verify_tls = conn.get("verifyTls", True)
    if not isinstance(verify_tls, bool):
        verify_tls = True
    client = ArangoClient(hosts=url, verify_override=verify_tls)
    return client.db(db_name, username=username, password=pw)


def _tooling_block(*, analysis: dict[str, Any] | None, snapshot: dict[str, Any] | None) -> dict[str, Any]:
    tooling: dict[str, Any] = {"contractVersion": CONTRACT_VERSION}
    if analysis and isinstance(analysis, dict):
        md = analysis.get("metadata") if isinstance(analysis.get("metadata"), dict) else {}
        if isinstance(md, dict):
            tooling["usedBaseline"] = bool(md.get("used_baseline"))
            tooling["repairAttempts"] = int(md.get("repair_attempts") or 0)
            if md.get("runId"):
                tooling["runId"] = md["runId"]
            if md.get("physicalSchemaFingerprint"):
                tooling["physicalSchemaFingerprint"] = md["physicalSchemaFingerprint"]
            if "cacheHit" in md:
                tooling["cacheHit"] = bool(md.get("cacheHit"))
    if snapshot and isinstance(snapshot, dict):
        raw_ver = snapshot.get("version")
        tooling["snapshotVersion"] = int(raw_ver or 0) if str(raw_ver or "").isdigit() else raw_ver
        tooling["snapshotFingerprint"] = fingerprint_physical_schema(snapshot, include_samples=False)
    tooling["libraryVersion"] = _library_version()
    return tooling


def _build_response(
    *,
    op: str,
    req_id: str | None,
    result: dict[str, Any],
    tooling: dict[str, Any],
) -> dict[str, Any]:
    resp: dict[str, Any] = {
        "contractVersion": CONTRACT_VERSION,
        "operation": op,
        "ok": True,
        "tooling": tooling,
        "result": result,
    }
    if req_id:
        resp["requestId"] = req_id
    errors = validate_response_v1(resp)
    if errors:
        raise SchemaAnalyzerError(f"Internal response validation failed: {errors}", code="INTERNAL_ERROR")
    return resp


def run_tool(request: dict[str, Any]) -> dict[str, Any]:
    """
    Stable v1 tool entrypoint. Accepts a request dict, returns a response dict.
    """
    errors = validate_request_v1(request)
    if errors:
        return {
            "contractVersion": CONTRACT_VERSION,
            "operation": request.get("operation"),
            "requestId": request.get("requestId"),
            "ok": False,
            "error": {"code": "INVALID_REQUEST", "message": "; ".join(errors)},
        }

    op: Operation = request["operation"]
    req_id = request.get("requestId")
    req_id = req_id if isinstance(req_id, str) and req_id else None

    try:
        logger.info("Processing operation=%s requestId=%s", op, req_id)

        if op in ("snapshot", "analyze"):
            conn = request["connection"]
            db = _connect_db(conn)

        analysis_options = request.get("analysisOptions") if isinstance(request.get("analysisOptions"), dict) else {}
        output_options = request.get("outputOptions") if isinstance(request.get("outputOptions"), dict) else {}

        if op == "snapshot":
            snapshot = snapshot_physical_schema(
                db,
                sample_limit_per_collection=int(analysis_options.get("sampleLimitPerCollection") or 0),
                include_samples_in_snapshot=bool(analysis_options.get("includeSamplesInSnapshot") or False),
            )
            return _build_response(
                op=op,
                req_id=req_id,
                result={"snapshot": snapshot},
                tooling=_tooling_block(analysis=None, snapshot=snapshot),
            )

        if op == "analyze":
            llm = request.get("llm") if isinstance(request.get("llm"), dict) else None
            raw_max_rep = analysis_options.get("maxRepairAttempts")
            max_repair = int(raw_max_rep) if raw_max_rep is not None else None
            sys_prompt = llm.get("systemPrompt") if llm else None
            if isinstance(sys_prompt, str) and not sys_prompt.strip():
                sys_prompt = None
            pv = llm.get("promptVersion") if llm else None
            prompt_version = pv if isinstance(pv, str) and pv.strip() else None
            analyzer = AgenticSchemaAnalyzer(
                llm_provider=(llm.get("provider") if llm else None),
                api_key=_get_api_key(llm),
                model=(llm.get("model") if llm else None),
                cache=(analysis_options.get("cache") if isinstance(analysis_options.get("cache"), dict) else None),
                cache_ttl_seconds=int(analysis_options.get("cacheTtlSeconds") or DEFAULT_CACHE_TTL_SECONDS),
                review_threshold=float(analysis_options.get("reviewThreshold") or DEFAULT_REVIEW_THRESHOLD),
                system_prompt=sys_prompt,
                prompt_version=prompt_version,
                max_repair_attempts=max_repair,
            )

            include_samples = bool(analysis_options.get("includeSamplesInSnapshot") or False)
            sample_limit = int(analysis_options.get("sampleLimitPerCollection") or 0)

            snapshot = snapshot_physical_schema(
                db,
                sample_limit_per_collection=sample_limit,
                include_samples_in_snapshot=include_samples,
            )

            analysis = analyzer.analyze_physical_schema(
                db,
                timeout_ms=int(analysis_options.get("timeoutMs") or DEFAULT_TIMEOUT_MS),
                sample_limit_per_collection=sample_limit,
                include_samples_in_snapshot=include_samples,
                use_cache=bool(analysis_options.get("useCache", True)),
                _snapshot=snapshot,
            )

            analysis_dict = {
                "conceptualSchema": analysis.conceptual_schema,
                "physicalMapping": analysis.physical_mapping,
                "metadata": analysis.metadata.model_dump(by_alias=True),
            }

            result: dict[str, Any] = {"analysis": analysis_dict}
            if bool(output_options.get("includeSnapshot") or False):
                result["snapshot"] = snapshot

            return _build_response(
                op=op,
                req_id=req_id,
                result=result,
                tooling=_tooling_block(analysis=analysis_dict, snapshot=snapshot),
            )

        # Transform operations
        input_obj = request.get("input") if isinstance(request.get("input"), dict) else {}
        analysis_in = input_obj.get("analysis")
        if not isinstance(analysis_in, dict):
            raise SchemaAnalyzerError("input.analysis is required", code="INVALID_ARGUMENT")

        if op == "export":
            raw_target = output_options.get("exportTarget")
            target = raw_target if isinstance(raw_target, str) else DEFAULT_EXPORT_TARGET
            out = export_mapping(analysis_in, target=target)
            return _build_response(
                op=op,
                req_id=req_id,
                result={"export": out},
                tooling=_tooling_block(analysis=analysis_in, snapshot=None),
            )

        if op == "docs":
            md = generate_schema_docs(analysis_in)
            return _build_response(
                op=op,
                req_id=req_id,
                result={"markdown": md},
                tooling=_tooling_block(analysis=analysis_in, snapshot=None),
            )

        if op == "owl":
            ttl = export_conceptual_model_as_owl_turtle(analysis_in)
            return _build_response(
                op=op,
                req_id=req_id,
                result={"turtle": ttl},
                tooling=_tooling_block(analysis=analysis_in, snapshot=None),
            )

        raise SchemaAnalyzerError(f"Unsupported operation: {op}", code="INVALID_ARGUMENT")

    except SchemaAnalyzerError as e:
        logger.warning("Operation %s failed: [%s] %s", op, e.code, e)
        resp: dict[str, Any] = {
            "contractVersion": CONTRACT_VERSION,
            "operation": op,
            "ok": False,
            "error": {"code": e.code or "ERROR", "message": str(e)},
        }
        if req_id:
            resp["requestId"] = req_id
        return resp
    except Exception:
        logger.exception("Unexpected error during operation %s", op)
        resp = {
            "contractVersion": CONTRACT_VERSION,
            "operation": op,
            "ok": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred. Check server logs for details.",
            },
        }
        if req_id:
            resp["requestId"] = req_id
        return resp

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, cast

from .defaults import (
    DEFAULT_ARANGO_URL,
    DEFAULT_ARANGO_USER,
    DEFAULT_EVAL_DATABASE,
    DEFAULT_EVAL_SAMPLE_LIMIT,
    DEFAULT_EVAL_SCALE,
    DEFAULT_TIMEOUT_MS,
    TOOL_ERROR_EXIT_CODE,
)
from .tool import run_tool


def _read_json(path: str | None) -> dict[str, Any]:
    if path:
        p = Path(path)
        return cast("dict[str, Any]", json.loads(p.read_text(encoding="utf-8")))
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("No input provided. Pass --request <file> or pipe JSON to stdin.")
    return cast("dict[str, Any]", json.loads(raw))


def _cmd_tool(args: argparse.Namespace) -> int:
    req = _read_json(args.request)
    resp = run_tool(req)
    text = json.dumps(resp, indent=2 if args.pretty else None, sort_keys=True)

    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0 if resp.get("ok") else TOOL_ERROR_EXIT_CODE


def _cmd_eval(args: argparse.Namespace) -> int:
    from arango import ArangoClient

    from .analyzer import AgenticSchemaAnalyzer
    from .eval import compare_reports, format_eval_table, run_eval, save_eval_report

    url = args.url or os.environ.get("ARANGO_URL", os.environ.get("ARANGO_HOST", DEFAULT_ARANGO_URL))
    user = args.user or os.environ.get("ARANGO_USER", DEFAULT_ARANGO_USER)
    password = args.password or os.environ.get("ARANGO_PASS", os.environ.get("ARANGO_PASSWORD", ""))
    db_name = args.database or os.environ.get("ARANGO_EVAL_DB", DEFAULT_EVAL_DATABASE)

    client = ArangoClient(hosts=url)
    sys_db = client.db("_system", username=user, password=password)
    if sys_db.has_database(db_name):
        sys_db.delete_database(db_name, ignore_missing=True)
    sys_db.create_database(db_name)
    db = client.db(db_name, username=user, password=password)

    analyzer = AgenticSchemaAnalyzer(
        llm_provider=args.provider,
        model=args.model,
    )

    domains = args.domains.split(",") if args.domains else None

    try:
        results = run_eval(
            db,
            analyzer=analyzer,
            domains=domains,
            sample_limit=args.sample_limit,
            timeout_ms=args.timeout_ms,
            scale=args.scale,
        )
    finally:
        if args.cleanup:
            with contextlib.suppress(Exception):
                sys_db.delete_database(db_name, ignore_missing=True)

    print(format_eval_table(results))

    if args.report:
        save_eval_report(results, args.report)
        print(f"\nReport saved to {args.report}")

    if args.baseline and args.report and Path(args.baseline).exists():
        print(f"\n--- Comparison vs {args.baseline} ---")
        print(compare_reports(args.report, args.baseline))

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arangodb-schema-analyzer", add_help=True)
    sub = parser.add_subparsers(dest="command")

    # Default tool mode (backwards compatible: no subcommand)
    parser.add_argument("--request", help="Path to request JSON. If omitted, read from stdin.")
    parser.add_argument("--out", help="Write response JSON to this path (default: stdout).")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output (indent=2).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging.")

    # Eval subcommand
    eval_p = sub.add_parser("eval", help="Run evaluation against domain packs.")
    eval_p.add_argument("--url", help="ArangoDB URL (or ARANGO_URL / ARANGO_HOST env).")
    eval_p.add_argument("--user", help="ArangoDB username (default: root).")
    eval_p.add_argument("--password", help="ArangoDB password (or ARANGO_PASS / ARANGO_PASSWORD env).")
    eval_p.add_argument("--database", help="Eval database name (default: schema_analyzer_eval).")
    eval_p.add_argument("--provider", default=None, help="LLM provider name.")
    eval_p.add_argument("--model", default=None, help="LLM model name.")
    eval_p.add_argument("--domains", default=None, help="Comma-separated domain names (default: all).")
    eval_p.add_argument(
        "--sample-limit",
        type=int,
        default=DEFAULT_EVAL_SAMPLE_LIMIT,
        help="Samples per collection.",
    )
    eval_p.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help="Timeout per analysis (ms).")
    eval_p.add_argument("--scale", type=int, default=DEFAULT_EVAL_SCALE, help="Scale factor for seeded data.")
    eval_p.add_argument("--report", default=None, help="Save JSON report to this path.")
    eval_p.add_argument("--baseline", default=None, help="Baseline report path for comparison.")
    eval_p.add_argument("--no-cleanup", dest="cleanup", action="store_false", default=True, help="Keep eval database.")
    eval_p.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging.")

    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

    if args.command == "eval":
        return _cmd_eval(args)

    return _cmd_tool(args)


if __name__ == "__main__":
    raise SystemExit(main())

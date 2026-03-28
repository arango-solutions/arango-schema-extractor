from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .errors import SchemaAnalyzerError
from .utils import extract_first_json_object
from .validation import validate_analysis_output

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkflowResult:
    data: dict[str, Any]
    repair_attempts: int


def _repair_prompt(*, validation_errors: list[str], previous_json: str) -> str:
    errs = "\n".join(f"- {e}" for e in validation_errors) if validation_errors else "- (unknown)"
    return (
        "Your previous output did not match the required JSON schema.\n"
        "Fix the JSON so it validates.\n\n"
        "Validation errors:\n"
        f"{errs}\n\n"
        "Previous JSON output:\n"
        f"{previous_json}\n\n"
        "Return ONLY the corrected JSON object. No markdown, no extra text."
    )


def run_generate_validate_repair(
    *,
    provider,
    model: str,
    system: str,
    prompt: str,
    timeout_ms: int,
    max_repair_attempts: int = 2,
) -> WorkflowResult:
    """
    Agentic loop: generate -> parse -> validate -> repair (if needed) -> finalize.
    Returns validated JSON (or raises on repeated failure).
    """
    repair_attempts = 0
    current_prompt = prompt

    while True:
        logger.info("LLM generate call: model=%s, timeout_ms=%d, attempt=%d", model, timeout_ms, repair_attempts + 1)
        resp = provider.generate(model=model, system=system, prompt=current_prompt, timeout_ms=timeout_ms)
        logger.debug("LLM response received: %d chars", len(resp.text or ""))

        try:
            json_str = extract_first_json_object(resp.text)
        except Exception as e:
            raise SchemaAnalyzerError("Failed to extract JSON from LLM output", code="PARSE_ERROR", cause=e)

        try:
            data = json.loads(json_str)
        except Exception as e:
            raise SchemaAnalyzerError("LLM output was not valid JSON", code="PARSE_ERROR", cause=e)

        errors = validate_analysis_output(data if isinstance(data, dict) else {})
        if not errors:
            logger.info("LLM output validated successfully after %d repair attempt(s)", repair_attempts)
            return WorkflowResult(data=data, repair_attempts=repair_attempts)

        if repair_attempts >= max_repair_attempts:
            logger.warning("Validation failed after %d repair attempts: %s", repair_attempts, errors)
            raise SchemaAnalyzerError(
                "LLM output failed schema validation after repair attempts",
                code="VALIDATION_ERROR",
                cause=None,
            )

        repair_attempts += 1
        logger.info("Validation failed, initiating repair attempt %d: %s", repair_attempts, errors)
        current_prompt = _repair_prompt(validation_errors=errors, previous_json=json_str)


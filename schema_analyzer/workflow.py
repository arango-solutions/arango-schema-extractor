from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from .defaults import MAX_REPAIR_ATTEMPTS, MAX_RETRIES, RETRY_BASE_DELAY
from .errors import SchemaAnalyzerError
from .providers.base import AsyncLLMProvider, LLMProvider, LLMResponse
from .utils import extract_first_json_object
from .validation import validate_analysis_output

logger = logging.getLogger(__name__)

_TRANSIENT_CODES = frozenset({"PROVIDER_ERROR"})


@dataclass(frozen=True)
class WorkflowResult:
    data: dict[str, Any]
    repair_attempts: int


def _is_transient(exc: SchemaAnalyzerError) -> bool:
    return exc.code in _TRANSIENT_CODES


def _retry_decision(
    exc: SchemaAnalyzerError,
    *,
    attempt: int,
    max_retries: int,
    base_delay: float,
) -> float:
    """Decide whether to retry a transient provider error and return the
    delay (in seconds) before the next attempt.

    Returns ``0.0`` when the caller must re-raise instead of retrying.
    Centralises the policy so the sync and async paths cannot drift apart.
    """
    if not _is_transient(exc) or attempt >= max_retries:
        return 0.0
    delay = base_delay * (2**attempt)
    logger.warning(
        "Transient provider error (attempt %d/%d), retrying in %.1fs: %s",
        attempt + 1,
        max_retries + 1,
        delay,
        exc,
    )
    return delay


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


def _parse_and_validate(resp: LLMResponse, repair_attempts: int, max_repair_attempts: int) -> WorkflowResult | str:
    """
    Parse and validate an LLM response.

    Returns a WorkflowResult on success, or the next prompt string if repair is needed.
    Raises SchemaAnalyzerError on fatal parse/validation failures.
    """
    try:
        json_str = extract_first_json_object(resp.text)
    except Exception as e:
        raise SchemaAnalyzerError("Failed to extract JSON from LLM output", code="PARSE_ERROR", cause=e) from e

    try:
        data = json.loads(json_str)
    except Exception as e:
        raise SchemaAnalyzerError("LLM output was not valid JSON", code="PARSE_ERROR", cause=e) from e

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

    logger.info("Validation failed, initiating repair attempt %d: %s", repair_attempts + 1, errors)
    return _repair_prompt(validation_errors=errors, previous_json=json_str)


def _call_with_retry(
    provider: LLMProvider,
    *,
    model: str,
    system: str,
    prompt: str,
    timeout_ms: int,
    max_retries: int = MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY,
) -> LLMResponse:
    """Call provider.generate with exponential backoff on transient failures."""
    last_exc: SchemaAnalyzerError | None = None
    for attempt in range(max_retries + 1):
        try:
            return provider.generate(model=model, system=system, prompt=prompt, timeout_ms=timeout_ms)
        except SchemaAnalyzerError as e:
            delay = _retry_decision(e, attempt=attempt, max_retries=max_retries, base_delay=base_delay)
            if delay <= 0:
                raise
            last_exc = e
            time.sleep(delay)
    raise last_exc  # pragma: no cover


def run_generate_validate_repair(
    *,
    provider: LLMProvider,
    model: str,
    system: str,
    prompt: str,
    timeout_ms: int,
    max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
    max_retries: int = MAX_RETRIES,
) -> WorkflowResult:
    """
    Agentic loop: generate -> parse -> validate -> repair (if needed) -> finalize.
    Each LLM call is wrapped with retry/backoff for transient provider errors.
    Returns validated JSON (or raises on repeated failure).
    """
    repair_attempts = 0
    current_prompt = prompt

    while True:
        logger.info("LLM generate call: model=%s, timeout_ms=%d, attempt=%d", model, timeout_ms, repair_attempts + 1)
        resp = _call_with_retry(
            provider,
            model=model,
            system=system,
            prompt=current_prompt,
            timeout_ms=timeout_ms,
            max_retries=max_retries,
        )
        logger.debug("LLM response received: %d chars", len(resp.text or ""))

        result = _parse_and_validate(resp, repair_attempts, max_repair_attempts)
        if isinstance(result, WorkflowResult):
            return result
        repair_attempts += 1
        current_prompt = result


async def _async_call_with_retry(
    provider: AsyncLLMProvider,
    *,
    model: str,
    system: str,
    prompt: str,
    timeout_ms: int,
    max_retries: int = MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY,
) -> LLMResponse:
    """Async version of _call_with_retry."""
    import asyncio

    last_exc: SchemaAnalyzerError | None = None
    for attempt in range(max_retries + 1):
        try:
            return await provider.agenerate(model=model, system=system, prompt=prompt, timeout_ms=timeout_ms)
        except SchemaAnalyzerError as e:
            delay = _retry_decision(e, attempt=attempt, max_retries=max_retries, base_delay=base_delay)
            if delay <= 0:
                raise
            last_exc = e
            await asyncio.sleep(delay)
    raise last_exc  # pragma: no cover


async def async_generate_validate_repair(
    *,
    provider: AsyncLLMProvider,
    model: str,
    system: str,
    prompt: str,
    timeout_ms: int,
    max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
    max_retries: int = MAX_RETRIES,
) -> WorkflowResult:
    """Async version of run_generate_validate_repair."""
    repair_attempts = 0
    current_prompt = prompt

    while True:
        logger.info("Async LLM generate: model=%s, timeout_ms=%d, attempt=%d", model, timeout_ms, repair_attempts + 1)
        resp = await _async_call_with_retry(
            provider,
            model=model,
            system=system,
            prompt=current_prompt,
            timeout_ms=timeout_ms,
            max_retries=max_retries,
        )
        logger.debug("LLM response received: %d chars", len(resp.text or ""))

        result = _parse_and_validate(resp, repair_attempts, max_repair_attempts)
        if isinstance(result, WorkflowResult):
            return result
        repair_attempts += 1
        current_prompt = result

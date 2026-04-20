from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_AQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def assert_aql_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not _AQL_IDENT_RE.match(value):
        raise ValueError(f"Invalid AQL identifier for {name}")


def stable_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def analysis_cache_storage_key(physical_fingerprint: str, *, llm_cache_segment: str | None) -> str:
    """
    Filesystem-safe cache filename stem.

    Baseline / no-LLM analysis is keyed only by the physical schema fingerprint.
    LLM runs also incorporate prompt version and effective system prompt so cache
    entries do not collide when prompts differ.
    """
    if not llm_cache_segment:
        return physical_fingerprint
    return sha256_hex(f"{physical_fingerprint}\n{llm_cache_segment}")


_IE_SINGULAR_ROOTS = frozenset(
    {
        "movie",
        "zombie",
        "cookie",
        "brownie",
        "rookie",
        "selfie",
        "genie",
        "smoothie",
        "collie",
        "magpie",
        "birdie",
        "calorie",
        "prairie",
        "reverie",
        "sortie",
        "lingerie",
    }
)


def singularize(name: str) -> str:
    """Best-effort English singularization for collection/entity names."""
    n = name.strip()
    if n.endswith("ies") and len(n) > 3:
        # Distinguish consonant+y→consonant+ies (city→cities) from root-ie+s (movie→movies).
        # Words whose singular ends in "-ie" should just drop the "s".
        without_s = n[:-1]
        if without_s.lower() in _IE_SINGULAR_ROOTS:
            return without_s
        return n[:-3] + "y"
    if n.endswith("sses") and len(n) > 4:
        return n[:-2]
    if n.endswith("s") and not n.endswith("ss") and len(n) > 1:
        return n[:-1]
    return n


def pascal_case(name: str) -> str:
    """Convert snake_case, kebab-case, or space-separated name to PascalCase."""
    parts = [p for p in str(name).replace("-", "_").replace(" ", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Unknown"


def extract_first_json_object(text: str) -> str:
    """
    Extract the first top-level JSON object from a string.
    Works with model outputs that include preamble/postamble.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object start found")

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError("Unterminated JSON object")

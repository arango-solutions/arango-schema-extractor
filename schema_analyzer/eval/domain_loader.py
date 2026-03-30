from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]  # repo root (schema_analyzer/..)
_DOMAINS_DIR = _ROOT / "domains"


def list_domains() -> list[str]:
    if not _DOMAINS_DIR.exists():
        return []
    out = []
    for p in sorted(_DOMAINS_DIR.iterdir()):
        if p.is_dir() and (p / "domain.json").exists():
            out.append(p.name)
    return out


def load_domain_spec(domain: str) -> dict[str, Any]:
    path = _DOMAINS_DIR / domain / "domain.json"
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("domain spec must be an object")
    return data


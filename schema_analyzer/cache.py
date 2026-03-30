from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .defaults import DEFAULT_CACHE_DIR
from .utils import stable_dumps

logger = logging.getLogger(__name__)


class AnalysisCache:
    def get(self, fingerprint: str) -> dict[str, Any] | None:  # pragma: no cover
        raise NotImplementedError

    def set(self, fingerprint: str, value: dict[str, Any], *, ttl_seconds: int) -> None:  # pragma: no cover
        raise NotImplementedError


@dataclass
class FilesystemCache(AnalysisCache):
    directory: Path

    def __post_init__(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, fingerprint: str) -> Path:
        return self.directory / f"{fingerprint}.json"

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        p = self._path(fingerprint)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text("utf-8"))
        except Exception:
            logger.warning("Corrupt or unreadable cache entry at %s, treating as miss", p)
            return None

        return data

    def set(self, fingerprint: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        p = self._path(fingerprint)
        payload = dict(value)
        payload["_cache"] = {"ttl_seconds": int(ttl_seconds)}
        try:
            p.write_text(stable_dumps(payload), "utf-8")
        except Exception:
            logger.warning("Failed to write cache entry at %s", p, exc_info=True)


def cache_from_config(cfg: dict[str, Any] | None) -> AnalysisCache | None:
    if not cfg:
        return None
    if cfg.get("type") == "filesystem":
        directory = cfg.get("directory") or DEFAULT_CACHE_DIR
        return FilesystemCache(Path(directory))
    return None


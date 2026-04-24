from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .defaults import CACHE_ROOT_ENV_VAR, DEFAULT_CACHE_DIR
from .errors import SchemaAnalyzerError
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
            raw = json.loads(p.read_text("utf-8"))
        except Exception:
            logger.warning("Corrupt or unreadable cache entry at %s, treating as miss", p)
            return None
        cache_meta = raw.get("_cache", {})
        generated_at = cache_meta.get("generated_at")
        ttl = cache_meta.get("ttl_seconds")
        if generated_at and ttl is not None:
            try:
                age = time.time() - generated_at
                if age > ttl:
                    logger.debug("Cache entry %s expired (age=%.0fs, ttl=%ds)", fingerprint, age, ttl)
                    return None
            except (TypeError, ValueError):
                pass
        return raw

    def set(self, fingerprint: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        p = self._path(fingerprint)
        payload = dict(value)
        payload["_cache"] = {"ttl_seconds": int(ttl_seconds), "generated_at": time.time()}
        try:
            p.write_text(stable_dumps(payload), "utf-8")
            # Restrict to owner-only on POSIX hosts so cached schema
            # metadata (which may include type-value samples) is not
            # world-readable on multi-tenant systems. No-op on Windows
            # where ``os.chmod`` semantics differ.
            if os.name == "posix":
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    logger.debug("Could not chmod 0o600 on cache file %s", p, exc_info=True)
        except Exception:
            logger.warning("Failed to write cache entry at %s", p, exc_info=True)


def _resolve_cache_directory(directory: str) -> Path:
    """
    Resolve a caller-supplied cache directory to an absolute path and,
    if ``SCHEMA_ANALYZER_CACHE_ROOT`` is set, enforce that the resolved
    path lies under it. Raises ``SchemaAnalyzerError(INVALID_ARGUMENT)``
    when the path escapes the configured root. Symlinks are resolved
    (``strict=False``) so ``..`` segments cannot bypass the check.
    """
    resolved = Path(directory).expanduser().resolve(strict=False)
    root_env = os.environ.get(CACHE_ROOT_ENV_VAR)
    if root_env:
        root = Path(root_env).expanduser().resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise SchemaAnalyzerError(
                f"cache directory {resolved} is outside the allowed root {root} "
                f"(set {CACHE_ROOT_ENV_VAR} appropriately to permit it)",
                code="INVALID_ARGUMENT",
            ) from exc
    return resolved


def cache_from_config(cfg: dict[str, Any] | None) -> AnalysisCache | None:
    if not cfg:
        return None
    if cfg.get("type") == "filesystem":
        directory = cfg.get("directory") or DEFAULT_CACHE_DIR
        return FilesystemCache(_resolve_cache_directory(str(directory)))
    return None

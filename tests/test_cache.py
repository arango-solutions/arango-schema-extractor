from __future__ import annotations

from pathlib import Path

from schema_analyzer.cache import FilesystemCache, cache_from_config


def test_filesystem_cache_roundtrip(tmp_path: Path):
    cache = FilesystemCache(directory=tmp_path / "cache")
    assert cache.get("nonexistent") is None

    data = {"conceptual_schema": {"entities": []}, "metadata": {"confidence": 0.5}}
    cache.set("fp123", data, ttl_seconds=3600)

    result = cache.get("fp123")
    assert result is not None
    assert result["metadata"]["confidence"] == 0.5
    assert result["_cache"]["ttl_seconds"] == 3600


def test_filesystem_cache_corrupt_file(tmp_path: Path):
    cache = FilesystemCache(directory=tmp_path / "cache")
    p = cache._path("corrupt")
    p.write_text("not valid json {{{", encoding="utf-8")
    assert cache.get("corrupt") is None


def test_filesystem_cache_missing_dir(tmp_path: Path):
    cache = FilesystemCache(directory=tmp_path / "deep" / "nested" / "cache")
    assert cache.directory.exists()


def test_cache_from_config_none():
    assert cache_from_config(None) is None


def test_cache_from_config_unknown_type():
    assert cache_from_config({"type": "redis"}) is None


def test_cache_from_config_filesystem(tmp_path: Path):
    cache = cache_from_config({"type": "filesystem", "directory": str(tmp_path / "c")})
    assert isinstance(cache, FilesystemCache)


def test_cache_from_config_filesystem_default_dir():
    cache = cache_from_config({"type": "filesystem"})
    assert isinstance(cache, FilesystemCache)

"""imhandler.blacklist — persistent image blacklist.

The sole persistence and matching implementation for hidden images (see
upgrades/blacklist.md). Stores absolute, resolved paths beneath a configured
image_root at cache_root()/blacklist.json, with inter-process locking and
atomic replacement so Django workers and CLI processes never lose an update
or observe partial JSON. Contains no Django types.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import AbstractSet

from . import cache, scanner

_STORE_NAME = 'blacklist.json'
_LOCK_NAME = '.blacklist.lock'
_VERSION = 1


class BlacklistError(Exception):
    """The blacklist store exists but is corrupt or unsupported."""


def _store_path() -> Path:
    return cache.cache_root() / _STORE_NAME


def _lock_path() -> Path:
    return cache.cache_root() / _LOCK_NAME


def _normalize(path: Path | str) -> Path:
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        raise ValueError(f'blacklist path must be absolute: {path}')
    normalized = expanded.resolve()
    if not any(
        normalized == root or normalized.is_relative_to(root)
        for root in cache.configured_image_roots()
    ):
        raise ValueError(f'blacklist path is not under a configured image_root: {normalized}')
    if normalized.suffix.lower() not in scanner.IMAGE_SUFFIXES:
        raise ValueError(f'blacklist path has an unsupported suffix: {normalized}')
    return normalized


def _validate_stored_entry(raw: str) -> Path:
    if not raw:
        raise BlacklistError('blacklist entry is empty')
    if '\x00' in raw:
        raise BlacklistError(f'blacklist entry contains a NUL byte: {raw!r}')
    if not Path(raw).is_absolute():
        raise BlacklistError(f'blacklist entry is not absolute: {raw!r}')
    if os.path.normpath(raw) != raw:
        raise BlacklistError(f'blacklist entry is not in canonical form: {raw!r}')
    if raw.startswith('//'):
        # POSIX (and normpath) special-cases exactly two leading slashes and
        # leaves them as-is, but Path.resolve() collapses them to one -- the
        # same string that passes the normpath check above would therefore
        # never match a resolve()-produced identity from _normalize() or the
        # serving endpoints.
        raise BlacklistError(f'blacklist entry has a non-canonical leading slash: {raw!r}')
    if Path(raw).suffix.lower() not in scanner.IMAGE_SUFFIXES:
        raise BlacklistError(f'blacklist entry has an unsupported suffix: {raw!r}')
    return Path(raw)


def load() -> frozenset[Path]:
    path = _store_path()
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            raw_text = fh.read()
    except FileNotFoundError:
        return frozenset()
    except UnicodeDecodeError as exc:
        raise BlacklistError(f'blacklist store is not valid UTF-8: {path}') from exc

    try:
        doc = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise BlacklistError(f'blacklist store is not valid JSON: {path}') from exc

    if not isinstance(doc, dict) or doc.get('version') != _VERSION:
        raise BlacklistError(f'blacklist store has an unsupported version: {path}')

    paths = doc.get('paths')
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise BlacklistError(f'blacklist store "paths" is not a list of strings: {path}')

    return frozenset(_validate_stored_entry(p) for p in paths)


def is_blocked(path: Path | str, blocked: AbstractSet[Path] | None = None) -> bool:
    if blocked is None:
        blocked = load()
    return Path(path) in blocked


def _write_atomic(blocked: AbstractSet[Path]) -> None:
    cache_dir = cache.cache_root()
    cache_dir.mkdir(parents=True, exist_ok=True)
    doc = {'version': _VERSION, 'paths': sorted(str(p) for p in blocked)}
    fd, tmp_name = tempfile.mkstemp(dir=cache_dir, prefix='.blacklist-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, _store_path())
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _update(normalized: Path, *, adding: bool) -> bool:
    cache_dir = cache.cache_root()
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path()
    with open(lock_path, 'a+') as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            current = load()
            if adding:
                if normalized in current:
                    return False
                updated = current | {normalized}
            else:
                if normalized not in current:
                    return False
                updated = current - {normalized}
            _write_atomic(updated)
            return True
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def add(path: Path | str) -> bool:
    normalized = _normalize(path)
    return _update(normalized, adding=True)


def remove(path: Path | str) -> bool:
    normalized = _normalize(path)
    return _update(normalized, adding=False)

"""Offline-reader active-link staging (spec 1.5, 4.5).

Maintains an app-controlled manifest mapping link names to canonical
sources, stored outside `DOCUMENT_VIEWER_ACTIVE_DIR` so reader-sync
software never sees it. Link and manifest updates happen under one
inter-process lock with atomic (temp-file + `os.replace()`) manifest
writes. The manifest is the sole source of truth for ownership; a bare
on-disk symlink is never trusted on its own.

Crash durability across the separate manifest and symlink operations is
deliberately not a v1 requirement: an interrupted operation may leave a
mismatch, which is reported clearly rather than silently guessed at, and
left for `documentview_reconcile_active` or a retry.
"""
import contextlib
import dataclasses
import fcntl
import json
import logging
import os
import stat
from pathlib import Path

from . import config, documents, paths

logger = logging.getLogger('documentview')

REASON_MISSING = 'missing'
REASON_NOT_A_FILE = 'not_a_file'
REASON_UNREADABLE = 'unreadable'
REASON_UNSUPPORTED = 'unsupported_type'

REASON_LABELS = {
    REASON_MISSING: 'the source file is missing',
    REASON_NOT_A_FILE: 'the source is no longer a regular file',
    REASON_UNREADABLE: 'the source is not readable',
    REASON_UNSUPPORTED: 'the source no longer has a supported suffix',
}


class ActiveError(Exception):
    pass


class CollisionError(ActiveError):
    """A different registered source, or an unfamiliar filesystem entry,
    already occupies the computed active-link name."""


class MismatchError(ActiveError):
    """Manifest and filesystem disagree in a way that must be reported
    rather than silently repaired."""


class ManifestError(ActiveError):
    """The manifest file exists but could not be read or parsed. Distinct
    from a simply-missing manifest (which legitimately means "no active
    links yet"): a corrupt or unreadable manifest must surface as a clear,
    repairable error rather than being silently treated as empty, which
    would make every existing managed link look foreign and block its
    normal removal."""


@dataclasses.dataclass
class RemoveResult:
    link_name: str
    reason: 'str | None'  # None means the source was valid when removed


@dataclasses.dataclass
class ReconcileIssue:
    link_name: str
    kind: str  # 'missing_symlink' | 'broken_source' | 'wrong_target' | 'invalid_entry' | 'foreign'
    detail: str
    repaired: bool = False


@contextlib.contextmanager
def _locked():
    config.validate_live()
    lock_path = config.active_manifest_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'a+b') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _read_manifest() -> dict:
    """A missing manifest legitimately means "no active links yet" and
    reads as `{}`. A manifest that exists but is unreadable or not valid
    JSON is a real problem -- raise `ManifestError` rather than silently
    treating it as empty (see `ManifestError`'s docstring).
    """
    manifest_path = config.active_manifest_path()
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            raw = f.read()
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise ManifestError(f'cannot read active-link manifest {manifest_path}: {e}') from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ManifestError(f'active-link manifest {manifest_path} is not valid JSON: {e}') from e
    if not isinstance(data, dict):
        raise ManifestError(f'active-link manifest {manifest_path} does not contain a JSON object')
    return data


def _write_manifest(data: dict) -> None:
    manifest_path = config.active_manifest_path()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest_path.with_name(f'{manifest_path.name}.{os.getpid()}.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, manifest_path)


def load_manifest_sources() -> dict:
    """`source rel_path -> link_name`, for cheap display-only active-state
    lookups (browse/view tiles). Not locked -- a plain read is fine for
    display; mutations below always re-check under the lock.

    Includes *both* the canonical source path and, when the document was
    activated through an in-hierarchy symlink, the path it was activated
    from, so the active badge appears wherever the collection lists that
    file rather than only under the symlink target's own directory.

    Unlike the mutating operations, a corrupt manifest here degrades to
    "no active badges shown" (logged) rather than breaking browsing --
    matching this app's general "malformed input degrades, never breaks
    browsing" posture (spec 1.2). `add_active`/`remove_active` do *not*
    swallow `ManifestError` this way: a mutation must see the real error.
    """
    try:
        manifest = _read_manifest()
    except ManifestError as e:
        logger.error('documentview: %s', e)
        return {}

    sources = {}
    for link_name, entry in manifest.items():
        for key in ('source', 'requested'):
            value = entry.get(key)
            if value:
                sources[value] = link_name
    return sources


def find_link_for_source(source_rel_path: str) -> 'str | None':
    return load_manifest_sources().get(source_rel_path)


def _resolve_source_candidate(source_rel_path: str) -> Path:
    """Safe, contained, symlink-resolved absolute path for a manifest
    `source` value. Raises `paths.PathError` if it is malformed (absolute,
    `..`, empty, NUL), missing, or would resolve outside
    `DOCUMENT_VIEWER_ROOT`.

    A manifest `source`/link name is untrusted input -- it can come from a
    corrupted or hand-edited manifest (spec 1.5) -- so it is never joined
    onto the root and trusted directly; `_classify_source()` and
    `reconcile()`'s repair path must not create or follow a link that
    escapes the collection root just because a traversal path was waved
    through as "missing" or "valid".
    """
    normalized = paths.normalize_rel_path(source_rel_path)
    candidate = config.root().joinpath(*normalized.split('/'))
    try:
        real = candidate.resolve(strict=True)
    except OSError as e:
        raise paths.PathError(str(e)) from e
    try:
        real.relative_to(config.root())
    except ValueError as e:
        raise paths.PathError('source escapes collection root') from e
    return real


def _classify_source(source_rel_path: str) -> 'str | None':
    """`None` means the source currently validates; otherwise one of the
    REASON_* constants, checked in the priority order spec 4.5 lists:
    missing, not-a-file, unreadable, unsupported-suffix.
    """
    if not source_rel_path:
        return REASON_MISSING
    try:
        real = _resolve_source_candidate(source_rel_path)
    except paths.PathError:
        return REASON_MISSING
    try:
        st = real.stat()
    except OSError:
        return REASON_MISSING

    if not stat.S_ISREG(st.st_mode):
        return REASON_NOT_A_FILE
    if not os.access(real, os.R_OK):
        return REASON_UNREADABLE
    if real.suffix.lower() not in documents.SUPPORTED_SUFFIXES:
        return REASON_UNSUPPORTED
    return None


def _link_matches_source(link_path: Path, expected_abs_source: Path) -> bool:
    if not link_path.is_symlink():
        return False
    try:
        return link_path.resolve(strict=True) == expected_abs_source
    except OSError:
        return False


def add_active(source_rel_path: str) -> str:
    """Create (or idempotently confirm) an active-reader link for the
    exact selected source format. Returns the link name.

    Raises `paths.PathError` if `source_rel_path` isn't a valid document,
    `CollisionError` if the computed name is taken by something else, or
    `MismatchError` if it's already registered to this source but the
    on-disk symlink is missing/wrong.
    """
    requested_source = paths.normalize_rel_path(source_rel_path)
    with paths.resolve_document(source_rel_path) as resolved:
        canonical_source = resolved.rel_path
        source_abs = resolved.abs_path
        link_name = source_abs.name

    with _locked():
        manifest = _read_manifest()
        active_dir = config.active_dir()
        active_dir.mkdir(parents=True, exist_ok=True)
        link_path = active_dir / link_name

        entry = manifest.get(link_name)
        if entry is not None and entry.get('source') == canonical_source:
            if not _link_matches_source(link_path, source_abs):
                raise MismatchError(
                    f'"{link_name}" is already registered to this source, but its symlink is missing or incorrect'
                )
            # Idempotent confirm. Still record a not-yet-seen requested
            # alias: e.g. the document was first activated as
            # `real/Book.epub` and is now also being activated as
            # `selected/Book.epub` -- without this, the curated directory's
            # UI would keep showing it as inactive forever (spec 1.5).
            if requested_source != canonical_source and entry.get('requested') != requested_source:
                entry['requested'] = requested_source
                manifest[link_name] = entry
                try:
                    _write_manifest(manifest)
                except OSError as e:
                    raise ActiveError(
                        f'"{link_name}" is already active, but failed to record the new alias: {e}'
                    ) from e
            return link_name

        if entry is not None:
            raise CollisionError(f'"{link_name}" is already active for a different source')

        if link_path.exists(follow_symlinks=False):
            raise CollisionError(f'"{link_name}" already exists in the active directory')

        try:
            os.symlink(source_abs, link_path)
        except OSError as e:
            raise ActiveError(f'failed to create active link "{link_name}": {e}') from e

        entry = {'source': canonical_source}
        if requested_source != canonical_source:
            # The user activated this file through an in-hierarchy symlink
            # (e.g. a curated `selected/` directory). Record that path too,
            # so the browse/detail pages recognize the document as active
            # under the path they actually list it at -- not only under the
            # symlink target's canonical path. `source` stays canonical and
            # remains the single identity used for collision, idempotency,
            # and source-validity checks.
            entry['requested'] = requested_source
        manifest[link_name] = entry
        try:
            _write_manifest(manifest)
        except OSError as e:
            raise ActiveError(f'created active link "{link_name}" but failed to update the manifest: {e}') from e

        return link_name


def _validate_link_name(link_name: str) -> None:
    if not link_name or '/' in link_name or link_name in ('.', '..') or '\x00' in link_name:
        raise ActiveError('invalid active link name')


def remove_active(link_name: str) -> RemoveResult:
    """Remove an app-owned active link. Only ever unlinks a symlink
    directly inside `DOCUMENT_VIEWER_ACTIVE_DIR` that is registered in the
    manifest -- never an arbitrary path, and never the link's target.

    Removal succeeds regardless of the registered source's current state;
    `RemoveResult.reason` reports *why* if it no longer validates.
    """
    _validate_link_name(link_name)

    with _locked():
        manifest = _read_manifest()
        entry = manifest.get(link_name)
        if entry is None:
            raise ActiveError(f'"{link_name}" is not an app-managed active link')

        active_dir = config.active_dir()
        dir_fd = os.open(active_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                st = os.lstat(link_name, dir_fd=dir_fd)
            except OSError as e:
                raise ActiveError(f'"{link_name}" is not present in the active directory: {e}') from e
            if not stat.S_ISLNK(st.st_mode):
                raise ActiveError(f'"{link_name}" is not a symlink; refusing to remove it')

            reason = _classify_source(entry['source'])

            try:
                os.unlink(link_name, dir_fd=dir_fd)
            except OSError as e:
                raise ActiveError(f'failed to remove active link "{link_name}": {e}') from e
        finally:
            os.close(dir_fd)

        del manifest[link_name]
        try:
            _write_manifest(manifest)
        except OSError as e:
            raise ActiveError(f'removed active link "{link_name}" but failed to update the manifest: {e}') from e

    return RemoveResult(link_name=link_name, reason=reason)


def reconcile(repair: bool = False) -> list:
    """Report (and, with `repair=True`, fix) manifest/filesystem
    disagreements. Never touches a foreign symlink (one present in
    `DOCUMENT_VIEWER_ACTIVE_DIR` but absent from the manifest) -- those are
    always reported only, matching spec 1.5's "no automatic adoption".
    """
    issues = []
    with _locked():
        manifest = _read_manifest()
        active_dir = config.active_dir()
        active_dir.mkdir(parents=True, exist_ok=True)

        on_disk = {entry.name for entry in active_dir.iterdir() if entry.is_symlink()}
        changed = False

        for link_name, entry in list(manifest.items()):
            try:
                _validate_link_name(link_name)
            except ActiveError:
                # A manifest key that isn't a bare filename (e.g. containing
                # `/` or `..`) can never correspond to a real app-managed
                # link -- active_dir / link_name is only ever computed for a
                # validated name below, so this entry is simply corrupt data
                # rather than something to repair a symlink for.
                if repair:
                    del manifest[link_name]
                    changed = True
                    issues.append(
                        ReconcileIssue(link_name, 'invalid_entry', 'invalid link name; manifest entry removed', repaired=True)
                    )
                else:
                    issues.append(ReconcileIssue(link_name, 'invalid_entry', 'invalid link name in manifest'))
                continue

            source_rel = entry.get('source')
            link_path = active_dir / link_name

            if not link_path.is_symlink():
                reason = _classify_source(source_rel) if source_rel else REASON_MISSING
                if reason is None:
                    if repair:
                        try:
                            real = _resolve_source_candidate(source_rel)
                            os.symlink(real, link_path)
                            issues.append(ReconcileIssue(link_name, 'missing_symlink', 'recreated', repaired=True))
                        except (paths.PathError, OSError) as e:
                            issues.append(ReconcileIssue(link_name, 'missing_symlink', f'recreate failed: {e}'))
                    else:
                        issues.append(
                            ReconcileIssue(link_name, 'missing_symlink', 'symlink missing; source still valid')
                        )
                else:
                    label = REASON_LABELS.get(reason, reason)
                    if repair:
                        del manifest[link_name]
                        changed = True
                        issues.append(
                            ReconcileIssue(
                                link_name, 'missing_symlink',
                                f'symlink missing; source invalid ({label}); manifest entry removed',
                                repaired=True,
                            )
                        )
                    else:
                        issues.append(
                            ReconcileIssue(link_name, 'missing_symlink', f'symlink missing; source invalid ({label})')
                        )
                continue

            reason = _classify_source(source_rel) if source_rel else REASON_MISSING
            if reason is not None:
                label = REASON_LABELS.get(reason, reason)
                if repair:
                    try:
                        os.unlink(link_path)
                    except OSError:
                        pass
                    del manifest[link_name]
                    changed = True
                    issues.append(
                        ReconcileIssue(link_name, 'broken_source', f'source invalid ({label}); removed', repaired=True)
                    )
                else:
                    issues.append(ReconcileIssue(link_name, 'broken_source', f'source invalid ({label})'))
                continue

            # Source validates and the link exists -- but does it actually
            # point at that source? A link silently replaced with a symlink
            # to something else would otherwise never be caught here, since
            # nothing above compares the link's target against the
            # registered source.
            try:
                expected = _resolve_source_candidate(source_rel)
            except paths.PathError:
                expected = None  # _classify_source already validated this; defensive only

            if expected is not None and not _link_matches_source(link_path, expected):
                if repair:
                    try:
                        os.unlink(link_path)
                        os.symlink(expected, link_path)
                        issues.append(
                            ReconcileIssue(link_name, 'wrong_target', 'symlink pointed elsewhere; relinked', repaired=True)
                        )
                    except OSError as e:
                        issues.append(ReconcileIssue(link_name, 'wrong_target', f'relink failed: {e}'))
                else:
                    issues.append(
                        ReconcileIssue(link_name, 'wrong_target', 'symlink target does not match the registered source')
                    )

        for name in sorted(on_disk - set(manifest)):
            issues.append(ReconcileIssue(name, 'foreign', 'symlink present but not registered; left in place'))

        if changed:
            _write_manifest(manifest)

    return issues

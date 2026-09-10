"""Exports-directory staging (copy-on-export model).

`DOCUMENT_VIEWER_EXPORTS_DIR` holds byte-for-byte copies of exported
documents -- not symlinks, so that sync software/devices which can't
follow a symlink (e.g. an e-reader mounted over MTP) still get real files.
Whatever regular file physically exists there *is* an export, full stop,
in the same "directory is authority" spirit the old symlink design used:
there is no separate manifest recording ownership or intent, and no
distinction is kept between a copy this app wrote and a file placed there
by hand -- both are equally exported and equally removable (see the app's
Security Model: this directory holds either app-written copies or files
the single operator placed there themselves, so there's nothing to
protect it against). add/remove operations acquire an `fcntl.flock` lock
on a sibling lock file (under `DOCUMENT_VIEWER_CACHE_DIR`, never inside
the exports directory itself -- see `config.active_lock_path()`) before
touching the directory.
"""
import contextlib
import dataclasses
import fcntl
import logging
import os
import shutil
import tempfile
from pathlib import Path

from . import config, paths

logger = logging.getLogger('documentview')


class ActiveError(Exception):
    pass


@dataclasses.dataclass
class RemoveResult:
    link_name: str


@contextlib.contextmanager
def _locked():
    config.validate_live()
    lock_path = config.active_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'a+b') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _validate_link_name(link_name: str) -> None:
    if not link_name or '/' in link_name or link_name in ('.', '..') or '\x00' in link_name:
        raise ActiveError('invalid export file name')


def exported_names() -> set:
    """Names of every non-hidden regular file currently in the exports
    directory, for browse/detail-page "exported" badges only. Every
    visible file counts -- directory-is-authority, and there's no way (or
    need) to tell a copy this app wrote apart from one placed by hand.
    """
    try:
        exports_dir = config.exports_dir()
        entries = list(os.scandir(exports_dir))
    except OSError:
        return set()

    names = set()
    for entry in entries:
        name = entry.name
        if name.startswith('.'):
            continue
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        names.add(name)
    return names


def add_active(source_rel_path: str) -> str:
    """Copy the exact selected source format's bytes into the exports
    directory under its own filename, replacing whatever (if anything)
    already occupies that name: latest write wins, the same conflict rule
    the old symlink design used. There's no manifest recording which
    source a given exported name came from, so an existing file there is
    always overwritten unconditionally rather than compared against the
    source and skipped when it merely looks unchanged -- two different
    source files can easily share a size and (at whatever mtime
    resolution the filesystem offers) an mtime, and a stale copy from a
    different source must never be mistaken for an up-to-date one.
    Returns the file's name in the exports directory.

    Raises `paths.PathError` if `source_rel_path` isn't a valid document,
    or `ActiveError` if the copy itself fails.
    """
    with paths.resolve_document(source_rel_path) as resolved:
        name = resolved.abs_path.name
        mtime_ns = resolved.mtime_ns

        with _locked():
            exports_dir = config.exports_dir()
            exports_dir.mkdir(parents=True, exist_ok=True)
            dest = exports_dir / name

            tmp_fd, tmp_name = tempfile.mkstemp(dir=exports_dir, prefix=f'.{name}.')
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(tmp_fd, 'wb') as tmp_file:
                    with os.fdopen(os.dup(resolved.fd), 'rb') as src_file:
                        src_file.seek(0)
                        shutil.copyfileobj(src_file, tmp_file)
                os.utime(tmp_path, ns=(mtime_ns, mtime_ns))
                os.replace(tmp_path, dest)
            except OSError as e:
                tmp_path.unlink(missing_ok=True)
                raise ActiveError(f'failed to copy "{name}" to the exports directory: {e}') from e

    return name


def remove_active(link_name: str) -> RemoveResult:
    """Remove an exported file. Only ever unlinks a directory entry
    directly inside `DOCUMENT_VIEWER_EXPORTS_DIR` by name -- never a
    directory (`unlink` fails on one, surfaced as `ActiveError`), and never
    touches the source document. Presence there is the only authorization
    needed; there is no separate registry to check membership against.
    """
    _validate_link_name(link_name)

    with _locked():
        exports_dir = config.exports_dir()
        dir_fd = os.open(exports_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                os.lstat(link_name, dir_fd=dir_fd)
            except OSError as e:
                raise ActiveError(f'"{link_name}" is not present in the exports directory: {e}') from e
            try:
                os.unlink(link_name, dir_fd=dir_fd)
            except OSError as e:
                raise ActiveError(f'failed to remove "{link_name}" from the exports directory: {e}') from e
        finally:
            os.close(dir_fd)

    return RemoveResult(link_name=link_name)

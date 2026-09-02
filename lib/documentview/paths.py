"""Collection-relative identifier resolution and the secure-open contract.

Two resolvers, matching spec 4.1:

- `resolve_directory(rel_path)` -- must exist and be a real directory.
- `resolve_document(rel_path)` -- must exist, be a regular file, have a
  supported suffix, and returns an already-open file descriptor callers
  read bytes from directly rather than re-opening the path later.

Symlink policy (confirmed against the real collection, 4.1): it links only
to files, never directories, and never chains symlink-to-symlink. So:

- `resolve_directory()` never needs symlink handling. Every path component
  is opened `O_NOFOLLOW` relative to the previous component's directory fd
  (pinned, immune to a swap once passed); a directory-position entry that
  is unexpectedly a symlink is hidden/rejected rather than followed.
- `resolve_document()` walks its directory components the same pinned way,
  then resolves only the final component: `O_NOFOLLOW` first; on `ELOOP`
  (the file itself is a symlink), the target is read and resolved (up to
  `DOCUMENT_VIEWER_MAX_SYMLINK_HOPS` hops), containment-checked against
  `DOCUMENT_VIEWER_ROOT`, and then the *real* file is opened `O_NOFOLLOW`
  relative to its own freshly-walked, pinned parent directory fd -- so
  nothing can substitute a different file at the last moment. There is a
  window between reading the symlink's target and that final open;
  concurrent hostile mutation of the collection is explicitly outside the
  threat model (4.1).

This is Linux/POSIX `openat()`-style and relies on `/proc/self/fd` to learn
a directory fd's real path; it matches this deployment's platform.
"""
import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import config
from .documents import SUPPORTED_SUFFIXES


class PathError(Exception):
    """Invalid, unsafe, or unresolvable collection-relative identifier."""


class NotFoundError(PathError):
    pass


@dataclass(frozen=True)
class ResolvedDirectory:
    rel_path: str
    abs_path: Path


@dataclass
class ResolvedDocument:
    rel_path: str
    abs_path: Path
    directory: Path
    suffix: str          # normalized lowercase, no leading dot
    fd: int
    mtime_ns: int
    size: int

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def _split_parts(rel_path, *, allow_empty):
    if rel_path is None:
        raise PathError('missing path')
    if '\x00' in rel_path:
        raise PathError('NUL byte in path')
    pure = PurePosixPath(rel_path)
    if pure.is_absolute():
        raise PathError('absolute path not allowed')
    parts = [part for part in pure.parts if part not in ('', '.')]
    if any(part == '..' for part in parts):
        raise PathError('traversal not allowed')
    if not parts and not allow_empty:
        raise PathError('empty path')
    return parts


def normalize_rel_path(rel_path: str) -> str:
    """Validate and canonicalize a collection-relative path *as written*,
    without touching the filesystem or following symlinks. Useful for
    recording the path a user actually acted on, alongside the resolved
    real path `resolve_document()` returns.
    """
    return '/'.join(_split_parts(rel_path, allow_empty=False))


def _open_root_fd():
    root = config.root()
    try:
        return os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as e:
        raise PathError(f'cannot open collection root: {e}') from e


def _open_dir_chain(start_fd, parts):
    """Walk `parts` one pinned, O_NOFOLLOW component at a time from `start_fd`.

    Returns a fd the caller owns and must close; never touches `start_fd`.
    """
    cur_fd = os.dup(start_fd)
    try:
        for part in parts:
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cur_fd)
            except OSError as e:
                raise NotFoundError(f'not a directory: {part}') from e
            os.close(cur_fd)
            cur_fd = next_fd
        return cur_fd
    except Exception:
        os.close(cur_fd)
        raise


def resolve_directory(rel_path: str) -> ResolvedDirectory:
    config.validate_live()
    parts = _split_parts(rel_path, allow_empty=True)
    root_fd = _open_root_fd()
    try:
        dir_fd = _open_dir_chain(root_fd, parts)
    finally:
        os.close(root_fd)
    os.close(dir_fd)
    abs_path = config.root().joinpath(*parts)
    return ResolvedDirectory(rel_path='/'.join(parts), abs_path=abs_path)


def _resolve_symlink_target(filename: str, parent_fd: int, max_hops: int) -> Path:
    """Follow a bounded chain of symlinks starting at `filename` inside
    `parent_fd`'s directory. Returns the final real (non-symlink) Path,
    without itself being race-free -- see module docstring. The caller
    re-validates and re-opens the result race-free afterward.
    """
    root = config.root()
    parent_real = Path(os.readlink(f'/proc/self/fd/{parent_fd}'))

    current_dir = parent_real
    current_name = filename
    hops = 0
    while True:
        full = current_dir / current_name
        if not full.is_symlink():
            break
        hops += 1
        if hops > max_hops:
            raise NotFoundError('too many symlink hops')
        try:
            target = os.readlink(full)
        except OSError as e:
            raise NotFoundError('broken symlink') from e
        target_path = Path(target) if os.path.isabs(target) else (current_dir / target)
        current_dir = Path(os.path.normpath(str(target_path.parent)))
        current_name = target_path.name

    final = current_dir / current_name
    try:
        real_final = final.resolve(strict=True)
    except OSError as e:
        raise NotFoundError('broken symlink target') from e
    try:
        real_final.relative_to(root)
    except ValueError as e:
        raise NotFoundError('symlink target escapes collection root') from e
    return real_final


def _open_final_component(parts):
    """Open the file named by collection-relative `parts`, resolving a
    final-component symlink race-free per the module docstring. Returns
    `(fd, abs_path)`; caller owns `fd`.
    """
    *dir_parts, filename = parts
    root_fd = _open_root_fd()
    try:
        parent_fd = _open_dir_chain(root_fd, dir_parts)
    finally:
        os.close(root_fd)

    try:
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            return fd, config.root().joinpath(*parts)
        except OSError as e:
            if e.errno != errno.ELOOP:
                raise NotFoundError(f'not found: {filename}') from e

        real_target = _resolve_symlink_target(
            filename, parent_fd, config.limit('DOCUMENT_VIEWER_MAX_SYMLINK_HOPS')
        )
    finally:
        os.close(parent_fd)

    target_parts = real_target.relative_to(config.root()).parts
    *target_dir_parts, target_filename = target_parts
    root_fd = _open_root_fd()
    try:
        target_parent_fd = _open_dir_chain(root_fd, target_dir_parts)
    finally:
        os.close(root_fd)
    try:
        fd = os.open(target_filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=target_parent_fd)
    except OSError as e:
        raise NotFoundError('symlink target changed during resolution') from e
    finally:
        os.close(target_parent_fd)
    return fd, real_target


def resolve_document(rel_path: str) -> ResolvedDocument:
    config.validate_live()
    parts = _split_parts(rel_path, allow_empty=False)

    fd, abs_path = _open_final_component(parts)
    try:
        suffix = abs_path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise PathError(f'unsupported suffix: {abs_path.suffix}')
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PathError('not a regular file')
    except Exception:
        os.close(fd)
        raise

    rel_display = '/'.join(abs_path.relative_to(config.root()).parts)
    return ResolvedDocument(
        rel_path=rel_display,
        abs_path=abs_path,
        directory=abs_path.parent,
        suffix=suffix[1:],
        fd=fd,
        mtime_ns=st.st_mtime_ns,
        size=st.st_size,
    )

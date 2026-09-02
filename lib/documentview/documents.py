"""Logical-document grouping and format-variant selection.

This module owns all basename-based format-variant grouping so it is never
duplicated in views, templates, cover extraction, or active-link code (spec
2.1). `FORMAT_PREFERENCE` is the single hardcoded preview/cover-selection
ordering shared by `representative_variant()` here and `covers.cover_for()`
-- spec 1.1 only requires this be isolated in one helper, not configurable,
so it is a plain module constant rather than a Django setting.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_SUFFIXES = frozenset({'.pdf', '.epub', '.cbz', '.md', '.txt'})
FORMAT_PREFERENCE = ('epub', 'pdf', 'cbz', 'md', 'txt')

# Image suffixes recognized inside a CBZ archive, used for both cover
# extraction (covers.py) and page preview (previews.py).
CBZ_IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')

_NUM_RE = re.compile(r'(\d+)')


def natural_sort_key(name: str):
    """`2.jpg` sorts before `10.jpg`."""
    return tuple(int(part) if part.isdigit() else part for part in _NUM_RE.split(name))


@dataclass(frozen=True)
class DirEntry:
    name: str
    rel_path: str


@dataclass(frozen=True)
class Variant:
    suffix: str        # normalized lowercase, no leading dot, e.g. 'epub'
    filename: str       # original filename on disk (exact case)
    rel_path: str        # collection-relative path to this exact file
    mtime_ns: int
    size: int


@dataclass
class LogicalDocument:
    basename: str
    directory: str                # collection-relative directory, '' for root
    variants: dict                # normalized suffix -> Variant
    errors: list = field(default_factory=list)

    @property
    def title(self):
        return self.basename

    @property
    def rel_path(self):
        """Collection-relative path of the deterministically chosen representative variant."""
        return representative_variant(self).rel_path

    def sort_key(self):
        return (natural_sort_key(self.basename), self.basename)


def strip_supported_suffix(filename: str):
    """Strip the single rightmost supported suffix, matched case-insensitively.

    `Book.tar.pdf` groups under basename `Book.tar` -- `.tar` is not itself a
    supported suffix and is never stripped. Returns `(basename, suffix)`
    with `suffix` normalized lowercase and without a leading dot, or `None`
    if `filename` has no supported suffix.
    """
    lowered = filename.lower()
    for suffix in SUPPORTED_SUFFIXES:
        if lowered.endswith(suffix):
            return filename[: -len(suffix)], suffix[1:]
    return None


def _visible_symlink_target(abs_path: Path):
    """Resolve a file-position symlink for display purposes only.

    Returns the resolved real Path if it is a regular file inside
    DOCUMENT_VIEWER_ROOT, else None (meaning: hide this entry, same as any
    other unsupported/escaping entry -- not an error, since the collection
    is expected to contain only in-hierarchy file symlinks).
    """
    from . import config

    root = config.root()
    try:
        target = abs_path.resolve(strict=True)
    except OSError:
        return None
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


def scan_directory(abs_dir: Path, rel_prefix: str):
    """List one already-validated collection directory.

    `abs_dir` / `rel_prefix` must come from `paths.resolve_directory()` (or
    the collection root itself, rel_prefix=''). Returns
    `(subdirs, documents)`, both in stable natural order.
    """
    subdirs = []
    by_basename = {}

    try:
        entries = list(os.scandir(abs_dir))
    except OSError:
        entries = []

    # `os.scandir` yields in arbitrary filesystem order. Sort by name so
    # that when two files normalize to the same format (`Book.pdf` and
    # `Book.PDF`), *which* one is kept as that format's variant is
    # deterministic across processes and filesystems rather than a
    # coin-flip -- the collision itself is still reported as a collection
    # error below, naming both files.
    entries.sort(key=lambda e: e.name)

    for entry in entries:
        name = entry.name
        if name.startswith('.'):
            continue
        entry_rel = f'{rel_prefix}/{name}' if rel_prefix else name

        try:
            is_symlink = entry.is_symlink()
        except OSError:
            continue

        if is_symlink:
            # Directories are never symlinks in this collection (4.1); hide
            # any entry that unexpectedly is one instead of following it.
            target = _visible_symlink_target(Path(abs_dir) / name)
            if target is None:
                continue
            try:
                st = target.stat()
            except OSError:
                continue
            is_dir = False
        else:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue

        if is_dir:
            subdirs.append(DirEntry(name=name, rel_path=entry_rel))
            continue

        if is_symlink:
            pass  # already confirmed a regular file above
        elif not entry.is_file(follow_symlinks=False):
            continue

        split = strip_supported_suffix(name)
        if split is None:
            continue
        basename, suffix = split

        variant = Variant(
            suffix=suffix,
            filename=name,
            rel_path=entry_rel,
            mtime_ns=st.st_mtime_ns,
            size=st.st_size,
        )
        doc = by_basename.setdefault(
            basename, LogicalDocument(basename=basename, directory=rel_prefix, variants={})
        )
        if suffix in doc.variants:
            doc.errors.append(
                f'duplicate ".{suffix}" variant ({doc.variants[suffix].filename} and {name})'
            )
            continue
        doc.variants[suffix] = variant

    subdirs.sort(key=lambda d: (natural_sort_key(d.name), d.name))
    documents = sorted(by_basename.values(), key=lambda d: d.sort_key())
    return subdirs, documents


def representative_variant(document: LogicalDocument) -> Variant:
    """Deterministically choose any one valid variant for logical `view`/`cover` URLs.

    Walks `FORMAT_PREFERENCE` -- the same ordering `covers.cover_for()` uses,
    not a second, independently-tunable ordering.
    """
    for suffix in FORMAT_PREFERENCE:
        if suffix in document.variants:
            return document.variants[suffix]
    raise ValueError('logical document has no variants')

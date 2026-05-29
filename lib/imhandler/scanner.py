from pathlib import Path

from .cache import image_root, image_root_entries
from .models import Album, ImageEntry


IMAGE_SUFFIXES: frozenset[str] = frozenset({
    '.avif', '.bmp', '.gif', '.heic', '.heif',
    '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp',
})


def scan(root: Path | str | None = None) -> Album:
    if root is None:
        root = image_root()
    root = Path(root).expanduser().resolve()
    return _scan_dir(root, root, 0)


def scan_all() -> Album:
    """Scan all configured roots.

    For a single root returns scan(root) unchanged (album_rel paths are just
    relative to that root, same as before).  For multiple roots wraps them in
    a virtual Album whose rel_path is '.' and whose direct children are the
    per-root albums; each child's rel_path is prefixed with the root's display
    name, so album URLs look like 'Exports/2023/vacation'.
    """
    entries = image_root_entries()
    if len(entries) == 1:
        return scan(entries[0][0])
    virtual = Album(
        path=entries[0][0],
        rel_path=Path('.'),
        name='Images',
        depth=0,
    )
    for root, name in entries:
        child = _scan_dir(root, root, 1)
        _prefix_album(child, Path(name))
        child.name = name
        virtual.children.append(child)
    return virtual


def _prefix_album(album: Album, prefix: Path) -> None:
    album.rel_path = prefix if str(album.rel_path) == '.' else prefix / album.rel_path
    for img in album.images:
        img.rel_path = prefix / img.rel_path
    for child in album.children:
        _prefix_album(child, prefix)


def _scan_dir(path: Path, root: Path, depth: int) -> Album:
    rel = path.relative_to(root) if path != root else Path('.')
    album = Album(path=path, rel_path=rel, name=path.name or str(path), depth=depth)

    try:
        entries = sorted(path.iterdir(), key=lambda p: p.name)
    except PermissionError:
        return album

    subdirs: list[Path] = []
    image_entries: list[ImageEntry] = []

    for entry in entries:
        if entry.name.startswith('._') or entry.name == '__MACOSX':
            continue
        if not entry.is_symlink() and entry.is_dir():
            subdirs.append(entry)
        elif not entry.is_symlink() and entry.is_file() and entry.suffix.lower() in IMAGE_SUFFIXES:
            image_entries.append(ImageEntry(
                path=entry,
                rel_path=entry.relative_to(root),
                mtime=entry.stat().st_mtime,
            ))

    if subdirs:
        # interior node — images silently ignored
        for subdir in subdirs:
            album.children.append(_scan_dir(subdir, root, depth + 1))
    else:
        # leaf node
        album.images = image_entries

    return album

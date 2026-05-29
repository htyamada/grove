import hashlib
from pathlib import Path

from PIL import Image

from .cache import thumbs_dir
from .models import ImageEntry

try:
    from pillow_heif import register_heif_opener  # type: ignore[import-untyped]
    register_heif_opener()
except ImportError:
    pass

_JPEG_QUALITY = 85


def _thumb_path(entry: ImageEntry, long_edge: int) -> Path:
    digest = hashlib.sha256(str(entry.path).encode()).hexdigest()
    return thumbs_dir() / digest[:2] / f'{digest}-{long_edge}.jpg'


def get_or_create(entry: ImageEntry, long_edge: int = 200) -> Path:
    dest = _thumb_path(entry, long_edge)
    if dest.exists() and dest.stat().st_mtime >= entry.mtime:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    img: Image.Image = Image.open(entry.path)
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    img.thumbnail((long_edge, long_edge))
    img.save(dest, 'JPEG', quality=_JPEG_QUALITY)
    return dest


def prewarm(entries: list[ImageEntry], long_edge: int = 200) -> None:
    for entry in entries:
        get_or_create(entry, long_edge)


def purge(root: Path | str | None = None, *, dry_run: bool = False) -> tuple[int, int, int, int]:
    """Remove cached thumbnails and DB records whose source image no longer exists.

    Scans root (defaulting to all configured image_roots) to build the set of live images,
    then removes stale thumbnails from cache_dir/thumbs/ and stale rows
    from the dedup DB (Images, ClusterMembership, and now-empty Clusters).

    Returns (thumbs_removed, thumb_errors, db_rows_removed, db_errors).
    In dry-run mode the counts reflect what would be removed; nothing is deleted.
    """
    from .scanner import scan  # local import to avoid circular at module level
    from .db import open_db
    from .cache import db_path, image_roots as _image_roots

    if root is None:
        roots = _image_roots()
    else:
        roots = [Path(root).expanduser().resolve()]

    live_entries: list[ImageEntry] = []
    for r in roots:
        live_entries.extend(scan(r).all_images())
    live_paths: set[str] = {str(e.path) for e in live_entries}
    live_hashes: set[str] = {
        hashlib.sha256(str(e.path).encode()).hexdigest() for e in live_entries
    }

    # --- thumbnails ---
    thumb_removed = 0
    thumb_errors = 0
    td = thumbs_dir()
    if td.exists():
        for thumb_file in td.rglob('*.jpg'):
            digest = thumb_file.stem.split('-')[0]
            if digest not in live_hashes:
                if dry_run:
                    thumb_removed += 1
                else:
                    try:
                        thumb_file.unlink()
                        thumb_removed += 1
                    except OSError as exc:
                        print(f'purge: {thumb_file}: {exc}', flush=True)
                        thumb_errors += 1

    # --- database ---
    db_removed = 0
    db_errors = 0
    try:
        dp = db_path()
        if dp.exists():
            conn = open_db(dp)
            rows = conn.execute('SELECT id, path FROM Images').fetchall()
            stale_ids = [r['id'] for r in rows if r['path'] not in live_paths]
            db_removed = len(stale_ids)
            if stale_ids and not dry_run:
                ph = ','.join('?' * len(stale_ids))
                conn.execute(f'DELETE FROM ClusterMembership WHERE image_id IN ({ph})', stale_ids)
                conn.execute(f'DELETE FROM Images WHERE id IN ({ph})', stale_ids)
                conn.execute(
                    'DELETE FROM Clusters WHERE id NOT IN '
                    '(SELECT DISTINCT cluster_id FROM ClusterMembership)'
                )
                conn.commit()
            conn.close()
    except Exception as exc:
        print(f'purge: db: {exc}', flush=True)
        db_errors += 1

    return thumb_removed, thumb_errors, db_removed, db_errors

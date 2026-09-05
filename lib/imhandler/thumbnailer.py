import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet

from PIL import Image

from .cache import thumbs_dir
from .models import ImageEntry

try:
    from pillow_heif import register_heif_opener  # type: ignore[import-untyped]
    register_heif_opener()
except ImportError:
    pass

_JPEG_QUALITY = 85


def _thumb_path_for(path: Path, long_edge: int) -> Path:
    digest = hashlib.sha256(str(path).encode()).hexdigest()
    return thumbs_dir() / digest[:2] / f'{digest}-{long_edge}.jpg'


def get_or_create(
    entry: ImageEntry, long_edge: int = 200, *, blocked: AbstractSet[Path] | None = None,
) -> Path:
    """Return the cached thumbnail path for entry, generating it if needed.

    Raises blacklist.BlockedImageError, before touching any file, if entry's
    path (canonicalized) is blocked. This is the explicit-path entry point
    section 2.2 requires to call is_blocked itself: entry.path may be a
    caller-supplied alias or contain a non-canonical segment, so it is
    resolved once here and that resolved path is used for both the
    membership check and the thumbnail digest (an alias and its target then
    always share one cache file).
    """
    from . import blacklist  # local import to avoid circular at module level

    resolved = entry.path.resolve()
    if blocked is None:
        blocked = blacklist.load_if_configured()
    if resolved in blocked:
        raise blacklist.BlockedImageError(f'image is blocked: {resolved}')

    dest = _thumb_path_for(resolved, long_edge)
    if dest.exists() and dest.stat().st_mtime >= entry.mtime:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    img: Image.Image = Image.open(entry.path)
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    img.thumbnail((long_edge, long_edge))
    img.save(dest, 'JPEG', quality=_JPEG_QUALITY)
    return dest


def prewarm(
    entries: list[ImageEntry], long_edge: int = 200, *, blocked: AbstractSet[Path] | None = None,
) -> None:
    from . import blacklist  # local import to avoid circular at module level

    if blocked is None:
        blocked = blacklist.load_if_configured()
    for entry in entries:
        if entry.path.resolve() in blocked:
            continue
        get_or_create(entry, long_edge, blocked=blocked)


@dataclass
class PurgeResult:
    thumbs_removed: int
    thumb_errors: int
    thumbs_skipped: bool
    db_removed: int
    db_errors: int
    clusters_collapsed: int


def purge(
    root: Path | str | None = None, *, dry_run: bool = False,
    blocked: AbstractSet[Path] | None = None,
) -> PurgeResult:
    """Remove cached thumbnails and DB records for images that are no longer
    live: missing from disk, or blocked.

    Scans root (defaulting to all configured image_roots) to build the set of
    live images -- the scan already excludes blocked paths (see scanner.scan),
    so a blocked image's thumbnail, Images row, and cluster memberships are
    removed by the same logic that already removes a deleted file's, and its
    source file is never touched.

    When root is given, the DB sweep only considers rows under that root (a
    stale row elsewhere survives) and the thumbnail sweep -- which cannot be
    scoped to a subtree, since a thumbnail filename carries no path -- is
    skipped entirely; run purge with no root to sweep thumbnails.

    Clusters left with zero or exactly one remaining member are removed.

    Returns a PurgeResult. In dry-run mode the counts reflect what would be
    removed; nothing is deleted.
    """
    from .scanner import scan  # local import to avoid circular at module level
    from .db import open_db
    from .cache import db_path, image_roots as _image_roots
    from . import blacklist

    if blocked is None:
        blocked = blacklist.load_if_configured()

    scoped = root is not None
    if root is None:
        roots = _image_roots()
    else:
        roots = [Path(root).expanduser().resolve()]

    live_entries: list[ImageEntry] = []
    for r in roots:
        live_entries.extend(scan(r, blocked=blocked).all_images())
    live_paths: set[str] = {str(e.path) for e in live_entries}
    live_hashes: set[str] = {
        hashlib.sha256(str(e.path).encode()).hexdigest() for e in live_entries
    }

    # --- thumbnails ---
    thumb_removed = 0
    thumb_errors = 0
    thumbs_skipped = scoped
    if not scoped:
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
    clusters_collapsed = 0
    try:
        dp = db_path()
        if dp.exists():
            conn = open_db(dp)
            rows = conn.execute('SELECT id, path FROM Images').fetchall()
            if scoped:
                scanned_root = roots[0]
                stale_ids = [
                    r['id'] for r in rows
                    if r['path'] not in live_paths and Path(r['path']).is_relative_to(scanned_root)
                ]
            else:
                stale_ids = [r['id'] for r in rows if r['path'] not in live_paths]
            db_removed = len(stale_ids)
            stale_id_set = set(stale_ids)

            membership_rows = conn.execute(
                'SELECT cm.cluster_id, cm.image_id, i.path FROM ClusterMembership cm'
                ' JOIN Images i ON i.id = cm.image_id'
            ).fetchall()

            # A scoped purge only has visibility into images under
            # scanned_root: a cluster with any member outside it must be
            # left untouched, or a member elsewhere could be miscounted as
            # "the last one left" and have its cluster collapsed even
            # though nothing about it changed in this run.
            if scoped:
                out_of_scope_clusters = {
                    m['cluster_id'] for m in membership_rows
                    if not Path(m['path']).is_relative_to(scanned_root)
                }
            else:
                out_of_scope_clusters = set()

            # Seed every in-scope cluster at 0 so one whose members are ALL
            # stale -- and which therefore contributes no surviving row
            # below -- is still counted, instead of silently vanishing from
            # remaining_by_cluster while the empty-cluster sweep deletes it
            # anyway.
            remaining_by_cluster: dict[int, int] = {
                m['cluster_id']: 0 for m in membership_rows
                if m['cluster_id'] not in out_of_scope_clusters
            }
            for m in membership_rows:
                if m['cluster_id'] in out_of_scope_clusters:
                    continue
                if m['image_id'] in stale_id_set:
                    continue
                remaining_by_cluster[m['cluster_id']] += 1
            collapse_cluster_ids = [
                cid for cid, count in remaining_by_cluster.items() if count <= 1
            ]

            # A Cluster row that already has zero ClusterMembership rows --
            # not because this run stales its last member, but because it
            # was already empty -- never appears in membership_rows at all,
            # so it can't be seeded into remaining_by_cluster above. The
            # trailing empty-cluster sweep below deletes it unconditionally
            # (it always has, scoped or not), so it must be counted here too.
            all_cluster_ids = {r['id'] for r in conn.execute('SELECT id FROM Clusters').fetchall()}
            already_empty_cluster_ids = all_cluster_ids - {m['cluster_id'] for m in membership_rows}

            clusters_collapsed = len(collapse_cluster_ids) + len(already_empty_cluster_ids)

            if not dry_run:
                if stale_ids:
                    ph = ','.join('?' * len(stale_ids))
                    conn.execute(f'DELETE FROM ClusterMembership WHERE image_id IN ({ph})', stale_ids)
                    conn.execute(f'DELETE FROM Images WHERE id IN ({ph})', stale_ids)
                if collapse_cluster_ids:
                    ph2 = ','.join('?' * len(collapse_cluster_ids))
                    conn.execute(f'DELETE FROM ClusterMembership WHERE cluster_id IN ({ph2})', collapse_cluster_ids)
                    conn.execute(f'DELETE FROM Clusters WHERE id IN ({ph2})', collapse_cluster_ids)
                conn.execute(
                    'DELETE FROM Clusters WHERE id NOT IN '
                    '(SELECT DISTINCT cluster_id FROM ClusterMembership)'
                )
                conn.commit()
            conn.close()
    except Exception as exc:
        print(f'purge: db: {exc}', flush=True)
        db_errors += 1

    return PurgeResult(
        thumbs_removed=thumb_removed,
        thumb_errors=thumb_errors,
        thumbs_skipped=thumbs_skipped,
        db_removed=db_removed,
        db_errors=db_errors,
        clusters_collapsed=clusters_collapsed,
    )

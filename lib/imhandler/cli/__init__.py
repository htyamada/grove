"""CLI logic for the imh tool.

Entry point calls main(prog).  Pass -q / --qat to use the 'qat' config
variant instead of the default 'hty7'.
"""

import argparse
import datetime
import sys
import time
from pathlib import Path

from hty7.config import AppConfig
from imhandler import appconfig
from imhandler.filter_sort import SortKey, filter_and_sort
from imhandler.models import Album
from imhandler.scanner import scan
from imhandler.thumbnailer import get_or_create, purge
from imhandler.db import open_db

_CONF = 'etc/imhandler.conf'


def _print_tree(album: Album, indent: int = 0) -> None:
    prefix = '  ' * indent
    label = str(album.path) if indent == 0 else album.name
    print(f'{prefix}{label}/ ({album.image_count()})')
    for child in album.children:
        _print_tree(child, indent + 1)
    for img in album.images:
        print(f'{prefix}  {img.path.name}')


def cmd_scan(args: argparse.Namespace, prog: str) -> None:
    root = Path(args.dir)
    if not root.is_dir():
        print(f'{prog} list: {args.dir}: not a directory', file=sys.stderr)
        sys.exit(1)

    album = scan(root)

    if args.count:
        images = album.all_images()
        if args.glob:
            images = filter_and_sort(images, glob=args.glob, sort=SortKey(args.sort))
        print(len(images))
        return

    if args.tree:
        _print_tree(album)
    else:
        images = filter_and_sort(album.all_images(), glob=args.glob, sort=SortKey(args.sort))
        for img in images:
            print(img.rel_path)


def cmd_thumb(args: argparse.Namespace, prog: str) -> None:
    if args.dir:
        dirs: list[str] = [args.dir]
    elif appconfig.image_roots:
        dirs = list(appconfig.image_roots)
    else:
        print(f'{prog} thumb: error: image_root must be set in {_CONF} or DIR given',
              file=sys.stderr)
        sys.exit(1)

    cache_dir = appconfig.cache_dir
    if not cache_dir:
        print(f'{prog} thumb: error: cache_dir must be set in {_CONF}', file=sys.stderr)
        sys.exit(1)

    all_entries = []
    for dir_arg in dirs:
        root = Path(dir_arg)
        if not root.is_dir():
            print(f'{prog} thumb: {dir_arg}: not a directory', file=sys.stderr)
            sys.exit(1)
        all_entries.extend(scan(root).all_images())

    if args.dry_run:
        print(f'{len(all_entries)} image(s) found, no thumbnails generated')
        return

    done = 0
    processed = 0
    error_msgs: list[str] = []
    total = len(all_entries)
    progress_interval = 5.0
    next_progress = time.monotonic() + progress_interval
    try:
        for entry in all_entries:
            try:
                dest = get_or_create(entry, args.size)
                if args.verbose:
                    print(f'  {dest}')
                done += 1
            except Exception as e:
                error_msgs.append(f'{entry.path}: {e}')
            processed += 1
            if not args.verbose:
                now = time.monotonic()
                if now >= next_progress:
                    pct = int(processed * 100 / total) if total else 100
                    print(
                        f'progress: {processed}/{total} ({pct}%), '
                        f'{len(error_msgs)} error(s), current: {entry.path}',
                        flush=True,
                    )
                    next_progress = now + progress_interval
    except KeyboardInterrupt:
        print(f'\n{prog} thumb: interrupted after {done} thumbnail(s)')
        sys.exit(1)

    if error_msgs:
        log_dir = Path(cache_dir) / 'logs'
        log_dir.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        log_path = log_dir / f'thumb-errors-{ts}.log'
        with open(log_path, 'w') as f:
            for msg in error_msgs:
                f.write(msg + '\n')
        print(f'{done} thumbnail(s) generated, {len(error_msgs)} error(s)')
        print(f'errors logged to {log_path}')
        sys.exit(1)
    else:
        print(f'{done} thumbnail(s) generated, 0 error(s)')


def cmd_embed(args: argparse.Namespace, prog: str) -> None:
    if not appconfig.cache_dir:
        print(f'{prog} embed: error: cache_dir must be set in {_CONF}', file=sys.stderr)
        sys.exit(1)
    if args.dir:
        dirs: list[str] = [args.dir]
    elif appconfig.image_roots:
        dirs = list(appconfig.image_roots)
    else:
        print(f'{prog} embed: error: image_root must be set in {_CONF} or DIR given',
              file=sys.stderr)
        sys.exit(1)

    roots: list[Path] = []
    for dir_arg in dirs:
        root = Path(dir_arg).resolve()
        if not root.is_dir():
            print(f'{prog} embed: {dir_arg}: not a directory', file=sys.stderr)
            sys.exit(1)
        if appconfig.image_roots:
            if not any(root.is_relative_to(Path(ir).resolve()) for ir in appconfig.image_roots):
                roots_str = ', '.join(appconfig.image_roots)
                print(f'{prog} embed: {dir_arg}: not under any image_root ({roots_str})',
                      file=sys.stderr)
                sys.exit(1)
        roots.append(root)

    # Import here so the heavy ML stack is only loaded when this command runs
    from imhandler.embedder import embed_images

    weights = Path(args.weights) if args.weights else None

    tier_kw: dict[str, float] = {}
    if args.lap_lo is not None:
        tier_kw['lap_lo'] = args.lap_lo
    if args.lap_hi is not None:
        tier_kw['lap_hi'] = args.lap_hi
    if args.hf_lo is not None:
        tier_kw['hf_lo'] = args.hf_lo
    if args.block_hi is not None:
        tier_kw['block_hi'] = args.block_hi
    if args.sc_hi is not None:
        tier_kw['sc_hi'] = args.sc_hi

    db = open_db(Path(args.db) if args.db else None)
    total_processed = 0
    total_skipped = 0
    try:
        for root in roots:
            processed, skipped = embed_images(
                root, db,
                model=args.model,
                batch_size=args.batch_size,
                weights_dir=weights,
                tier_thresholds=tier_kw or None,
            )
            total_processed += processed
            total_skipped += skipped
    except KeyboardInterrupt:
        print(f'\n{prog} embed: interrupted')
        sys.exit(1)

    print(f'{total_processed} image(s) embedded, {total_skipped} skipped (up to date)')


def cmd_cluster(args: argparse.Namespace, prog: str) -> None:
    if not appconfig.cache_dir:
        print(f'{prog} cluster: error: cache_dir must be set in {_CONF}', file=sys.stderr)
        sys.exit(1)

    from imhandler.clusterer import cluster_images

    db = open_db(Path(args.db) if args.db else None)
    n = cluster_images(db, threshold=args.threshold, model=args.model)
    print(f'{n} cluster(s) written '
          f'(model={args.model}, threshold={args.threshold:.3f})')


def cmd_report(args: argparse.Namespace, prog: str) -> None:
    if not appconfig.cache_dir:
        print(f'{prog} report: error: cache_dir must be set in {_CONF}', file=sys.stderr)
        sys.exit(1)

    from imhandler.db import get_clusters, get_cluster_members

    db = open_db(Path(args.db) if args.db else None)
    clusters = get_clusters(db, model=args.model or None, threshold=args.threshold)
    if not clusters:
        print('No clusters found.')
        return

    out = open(args.output, 'w') if args.output else sys.stdout
    try:
        tier_labels = ['clean', 'degraded', 'heavily degraded']
        for cluster in clusters:
            members = get_cluster_members(db, cluster['id'])
            print(
                f'--- cluster {cluster["id"]} '
                f'model={cluster["model_used"]} '
                f'threshold={cluster["threshold_used"]:.3f} '
                f'{cluster["created_at"]} ---',
                file=out,
            )
            for m in members:
                tier = m['quality_tier']
                lap = m['laplacian_score']
                dims = (f'{m["width"]}x{m["height"]}' if m['width'] else '?x?')
                tier_str = tier_labels[tier] if tier is not None else 'unknown'
                lap_str = f'{lap:.4f}' if lap is not None else 'n/a   '
                rank_mark = '*' if m['quality_rank'] == 0 else ' '
                print(
                    f'  {rank_mark}[{m["quality_rank"]}]'
                    f' {tier_str:18s} lap={lap_str} {dims:>12s}'
                    f'  {m["path"]}',
                    file=out,
                )
            print(file=out)
    finally:
        if args.output:
            out.close()

    if args.output:
        print(f'report written to {args.output}')


def cmd_purge(args: argparse.Namespace, prog: str) -> None:
    if not appconfig.cache_dir:
        print(f'{prog} purge: error: cache_dir must be set in {_CONF}', file=sys.stderr)
        sys.exit(1)

    root = Path(args.dir) if args.dir else None
    if root is not None and not root.is_dir():
        print(f'{prog} purge: {args.dir}: not a directory', file=sys.stderr)
        sys.exit(1)

    try:
        thumb_removed, thumb_errors, db_removed, db_errors = purge(root, dry_run=args.dry_run)
    except EnvironmentError as e:
        print(f'{prog} purge: {e}', file=sys.stderr)
        sys.exit(1)

    label = 'would remove' if args.dry_run else 'removed'
    print(f'{thumb_removed} thumbnail(s) {label}, {thumb_errors} error(s)')
    print(f'{db_removed} DB record(s) {label}, {db_errors} error(s)')
    if thumb_errors or db_errors:
        sys.exit(1)


def main(prog: str = 'imh', ac: AppConfig | None = None) -> None:
    parser = argparse.ArgumentParser(prog=prog, description='Image collection tools.')
    parser.add_argument('-q', '--qat', action='store_true',
        help='use qat configuration variant instead of hty7')
    sub = parser.add_subparsers(dest='command', metavar='COMMAND')
    sub.required = False

    p_scan = sub.add_parser('list', aliases=['ls'], help='list image files in a directory tree')
    p_scan.add_argument('dir', metavar='DIR', help='root directory to scan')
    p_scan.add_argument('-t', '--tree', action='store_true',
        help='display as album tree instead of flat list')
    p_scan.add_argument('-g', '--glob', metavar='PATTERN',
        help='filter filenames by glob pattern')
    p_scan.add_argument('--sort', metavar='KEY', default='name',
        choices=['name', 'mtime', 'size'],
        help='sort order: name (default), mtime, size')
    p_scan.add_argument('--count', action='store_true',
        help='print total image count only')

    p_thumb = sub.add_parser('thumb', help='generate thumbnails for a directory tree')
    p_thumb.add_argument('dir', metavar='DIR', nargs='?', default=None,
        help='root directory to scan (default: image_root from etc/imhandler.conf)')
    p_thumb.add_argument('--size', metavar='N', type=int, default=200,
        help='thumbnail long edge in pixels (default: 200)')
    p_thumb.add_argument('-n', '--dry-run', action='store_true',
        help='count images without generating thumbnails')
    p_thumb.add_argument('-v', '--verbose', action='store_true',
        help='print each thumbnail path as it is created')

    p_purge = sub.add_parser('purge',
        help='remove cached thumbnails and DB records for images that no longer exist')
    p_purge.add_argument('dir', metavar='DIR', nargs='?', default=None,
        help='root directory to scan (default: image_root from etc/imhandler.conf)')
    p_purge.add_argument('-n', '--dry-run', action='store_true',
        help='report what would be removed without deleting anything')

    p_embed = sub.add_parser('embed',
        help='compute embeddings and quality metrics; store in database')
    p_embed.add_argument('dir', metavar='DIR', nargs='?', default=None,
        help='root directory to scan (default: image_root from etc/imhandler.conf)')
    p_embed.add_argument('--model', metavar='MODEL', default='both',
        choices=['clip', 'sscd', 'both'],
        help='embedding model(s) to compute: clip, sscd, or both (default)')
    p_embed.add_argument('--db', metavar='PATH', default=None,
        help='database path (default: cache_dir/db/dedup.db)')
    p_embed.add_argument('--weights', metavar='DIR', default=None,
        help='model weights directory (default: cache_dir/weights)')
    p_embed.add_argument('--batch-size', metavar='N', type=int, default=32,
        help='images per embedding batch (default: 32)')
    # Quality tier threshold overrides (optional tuning knobs)
    p_embed.add_argument('--lap-lo', metavar='F', type=float, default=None,
        help='laplacian_score below this → heavily blurry (default: 0.0005)')
    p_embed.add_argument('--lap-hi', metavar='F', type=float, default=None,
        help='laplacian_score below this → slightly blurry (default: 0.002)')
    p_embed.add_argument('--hf-lo', metavar='F', type=float, default=None,
        help='hf_power_ratio below this → possible upscaling (default: 0.65)')
    p_embed.add_argument('--block-hi', metavar='F', type=float, default=None,
        help='blocking_score above this → JPEG artifacts (default: 2.0)')
    p_embed.add_argument('--sc-hi', metavar='F', type=float, default=None,
        help='sharpness_consistency above this → inconsistent (default: 1.5)')

    p_cluster = sub.add_parser('cluster',
        help='cluster images by embedding similarity; write results to database')
    p_cluster.add_argument('--model', metavar='MODEL', default='clip',
        choices=['clip', 'sscd'],
        help='embedding to use for clustering: clip (default) or sscd')
    p_cluster.add_argument('--threshold', metavar='F', type=float, default=0.85,
        help='cosine similarity threshold (default: 0.85)')
    p_cluster.add_argument('--db', metavar='PATH', default=None,
        help='database path (default: cache_dir/db/dedup.db)')

    p_report = sub.add_parser('report',
        help='print image clusters from the database')
    p_report.add_argument('--model', metavar='MODEL', default=None,
        choices=['clip', 'sscd'],
        help='filter to clusters from this model')
    p_report.add_argument('--threshold', metavar='F', type=float, default=None,
        help='filter to clusters with this threshold')
    p_report.add_argument('--db', metavar='PATH', default=None,
        help='database path (default: cache_dir/db/dedup.db)')
    p_report.add_argument('-o', '--output', metavar='FILE', default=None,
        help='write report to FILE instead of stdout')

    args = parser.parse_args()
    if ac is None:
        raise RuntimeError("AppConfig must be provided")
    appconfig.init(ac)
    match args.command:
        case 'list' | 'ls':
            cmd_scan(args, prog)
        case 'thumb':
            cmd_thumb(args, prog)
        case 'purge':
            cmd_purge(args, prog)
        case 'embed':
            cmd_embed(args, prog)
        case 'cluster':
            cmd_cluster(args, prog)
        case 'report':
            cmd_report(args, prog)
        case _:
            print(f'usage: {prog} COMMAND [options] [DIR]')
            print()
            print('commands:')
            print('  list, ls   list image files in a directory tree')
            print('  thumb      generate thumbnails for a directory tree')
            print('  purge      remove cached thumbnails and DB records for images that no longer exist')
            print('  embed      compute embeddings and quality metrics; store in database')
            print('  cluster    cluster images by embedding similarity')
            print('  report     print image clusters from the database')

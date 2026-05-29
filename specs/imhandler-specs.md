# imhandler — Library API Reference

The `imhandler` package lives in `lib/imhandler/`. It is
safe to import from any frontend. All modules require Python 3.12+.

---

## Quick start

```python
from imhandler.scanner import scan
from imhandler.filter_sort import filter_and_sort, SortKey
from imhandler.thumbnailer import prewarm

album = scan('/home/yamada/Photos')
images = filter_and_sort(album.images, sort=SortKey.MTIME)
prewarm(images)
```

Dedup pipeline:

```python
from imhandler.db import open_db
from imhandler.embedder import embed_images
from imhandler.clusterer import cluster_images

conn = open_db()
embed_images('/home/yamada/Photos', conn)
n = cluster_images(conn, threshold=0.85, model='clip')
```

---

## `imhandler.appconfig` — Path configuration

```python
from hty7.config import AppConfig
from imhandler import appconfig
ac = AppConfig('etc/imhandler.conf', variant='hty7')
appconfig.init(ac)
```

Call `init(ac)` once at frontend startup with an `AppConfig` instance
(from `hty7.config`) before using any other `imhandler` module. The
variant and conf path are baked into `ac` at construction; `init()`
extracts `image_root` and `cache_dir` from the appropriate section.

Module-level globals set by `init()`:

| Name | Type | Description |
|------|------|-------------|
| `image_root` | `str` | Configured image directory (empty string if unset) |
| `cache_dir` | `str` | Configured cache directory (empty string if unset) |

---

## `imhandler.cache` — Cache directory helpers

All paths are resolved under `appconfig.cache_dir`. Every function raises
`EnvironmentError` if the relevant config value is empty.

```python
image_root() -> Path
```
Return `Path(appconfig.image_root)`, resolved to an absolute path. Raises
`EnvironmentError` if `image_root` is unset or the directory does not exist.

```python
cache_root() -> Path
```
Return `Path(appconfig.cache_dir)`.

```python
thumbs_dir() -> Path
```
Return `cache_dir/thumbs`.

```python
db_path() -> Path
```
Return `cache_dir/db/dedup.db`.

```python
weights_dir() -> Path
```
Return `cache_dir/weights`.

---

## `imhandler.models` — Data classes

```python
from imhandler.models import ImageEntry, Album
```

### `ImageEntry`

```python
@dataclass
class ImageEntry:
    path:     Path   # absolute path to the image file
    rel_path: Path   # path relative to the scan root
    mtime:    float  # st_mtime of the file
```

### `Album`

```python
@dataclass
class Album:
    path:     Path         # absolute path to the directory
    rel_path: Path         # path relative to the scan root ('.' for root)
    name:     str          # directory name
    depth:    int          # depth from scan root (0 = root)
    children: list[Album]  # subdirectories (interior nodes only)
    images:   list[ImageEntry]  # images (leaf nodes only)
```

An `Album` is either an interior node (has `children`, `images` is empty)
or a leaf node (has `images`, `children` is empty).

```python
album.image_count() -> int
```
Total image count for this album and all descendants.

```python
album.find(rel_path: Path | str) -> Album | None
```
Search the subtree depth-first for the album whose `rel_path` matches.
Returns `None` if not found.

```python
album.first_leaf() -> Album | None
```
Return the first leaf album (one with images) in depth-first order, or
`None` if the subtree contains no images.

```python
album.all_images() -> list[ImageEntry]
```
Return every `ImageEntry` in the subtree, depth-first.

---

## `imhandler.scanner` — Directory scanner

```python
from imhandler.scanner import scan
```

```python
scan(root: Path | str | None = None) -> Album
```

Walk `root` recursively and return an `Album` tree. `root` is resolved to
an absolute path before scanning. If `root` is `None`, `appconfig.image_root`
is used via `image_root()`; an `EnvironmentError` is raised if it is unset.

- Directories that contain subdirectories are **interior nodes**: their
  `children` are populated and any image files they contain are ignored.
- Directories with no subdirectories are **leaf nodes**: their `images` are
  populated.
- macOS metadata is silently skipped: `._*` files and `__MACOSX` directories.
- Symlinks are not followed.
- `PermissionError` on a directory returns an empty album for that node.

Supported suffixes: `.avif`, `.bmp`, `.gif`, `.heic`, `.heif`, `.jpg`,
`.jpeg`, `.png`, `.tif`, `.tiff`, `.webp`.

---

## `imhandler.filter_sort` — Filtering and sorting

```python
from imhandler.filter_sort import filter_images, sort_images, filter_and_sort, SortKey
```

### `SortKey`

```python
class SortKey(Enum):
    NAME  = 'name'
    MTIME = 'mtime'
    SIZE  = 'size'
```

### Functions

```python
filter_images(
    images: Sequence[ImageEntry],
    glob: str | None = None,
    mtime_after: float | None = None,
    mtime_before: float | None = None,
) -> list[ImageEntry]
```

Filter by glob pattern (matched against filename only), and/or mtime
bounds. All criteria are ANDed.

```python
sort_images(images: Sequence[ImageEntry], key: SortKey = SortKey.NAME) -> list[ImageEntry]
```

Return a sorted copy. `SortKey.SIZE` calls `stat()` per entry.

```python
filter_and_sort(
    images: Sequence[ImageEntry],
    glob: str | None = None,
    mtime_after: float | None = None,
    mtime_before: float | None = None,
    sort: SortKey = SortKey.NAME,
) -> list[ImageEntry]
```

Convenience wrapper: filter then sort.

---

## `imhandler.thumbnailer` — Thumbnail cache

```python
from imhandler.thumbnailer import get_or_create, prewarm
```

Requires `cache_dir` to be configured. Thumbnails are stored as JPEG at:

```
cache_dir/thumbs/<xx>/<sha256>-<size>.jpg
```

where `<sha256>` is the SHA-256 of the absolute image path string and
`<xx>` is its first two hex characters.

A cached thumbnail is considered valid if its mtime is ≥ the source
file's mtime. Outdated thumbnails are regenerated in place.

HEIC/HEIF support is enabled automatically if `pillow-heif` is installed.

```python
get_or_create(entry: ImageEntry, long_edge: int = 200) -> Path
```

Return the path to the thumbnail, generating it if absent or stale.
Creates cache subdirectories as needed.

```python
prewarm(entries: list[ImageEntry], long_edge: int = 200) -> None
```

Call `get_or_create` for each entry. Errors propagate from individual
entries.

```python
purge(root: Path | str | None = None, *, dry_run: bool = False) -> tuple[int, int, int, int]
```

Scan `root` (defaulting to `appconfig.image_root`) for current images, then
walk `cache_dir/thumbs/` and delete every thumbnail whose source image is no
longer present; also remove database records for missing images. Returns
`(thumb_removed, thumb_errors, db_removed, db_errors)`. When `dry_run` is
`True`, counts what would be removed without deleting anything. Requires both
`image_root` and `cache_dir` to be configured.

---

## `imhandler.db` — SQLite database

```python
from imhandler.db import open_db
```

Used by the dedup pipeline. Gallery tools do not use the database.

```python
open_db(path: Path | None = None) -> sqlite3.Connection
```

Open (or create) the SQLite database. If `path` is `None`, uses
`cache_dir/db/dedup.db`. Creates parent directories as needed.
Returns a connection with `row_factory = sqlite3.Row`, WAL journal mode,
and foreign keys enabled. Schema is initialised on first open.

### Schema

```
Images(id, path, mtime, width, height,
       clip_embedding BLOB, sscd_embedding BLOB,
       laplacian_score, hf_power_ratio, blocking_score,
       sharpness_consistency, quality_tier,
       UNIQUE(path, mtime))

Clusters(id, threshold_used, model_used, created_at)

ClusterMembership(cluster_id, image_id, quality_rank,
                  PRIMARY KEY(cluster_id, image_id))
```

### Query helpers

```python
get_clusters(conn, *, model=None, threshold=None) -> list[Row]
```
Return `Clusters` rows ordered by `created_at DESC, id`. Both filter
arguments are optional.

```python
get_cluster_member_rows(conn, *, model=None, threshold=None) -> list[Row]
```
Return a flat join of `Clusters + ClusterMembership + Images` for all
matching clusters, ordered by `cluster_id, quality_rank`. Columns include
all image metric fields. Used by the compare view to load all members in
one query.

```python
get_cluster_members(conn, cluster_id) -> list[Row]
```
Return `ClusterMembership + Images` rows for a single cluster, ordered by
`quality_rank`. Columns: `image_id, path, width, height, laplacian_score,
hf_power_ratio, blocking_score, sharpness_consistency, quality_tier,
quality_rank`.

```python
cleanup_missing_members(conn, cluster_id) -> tuple[list[int], int]
```
Delete `ClusterMembership` and `Images` rows for files that no longer
exist on disk. Returns `(missing_ids, remaining_count)`. If
`remaining_count <= 1` the caller should delete the cluster itself.

```python
get_embedded_paths(conn, paths: Iterable[str]) -> set[str]
```
Return the subset of `paths` that have at least one non-null embedding
(`clip_embedding` or `sscd_embedding`) in the database.

---

## `imhandler.embedder` — Embedding and quality metrics

```python
from imhandler.embedder import compute_quality_metrics, embed_images
```

Requires the `~/opt/web` venv (PyTorch, open_clip, scipy).

```python
compute_quality_metrics(
    img: Image.Image,
    *,
    lap_lo: float = 0.0005,
    lap_hi: float = 0.002,
    hf_lo: float = 0.65,
    block_hi: float = 2.0,
    sc_hi: float = 1.5,
) -> dict[str, float | int]
```

Compute quality metrics for a PIL image using numpy/scipy only (no neural
models). Returns a dict with keys `laplacian_score`, `hf_power_ratio`,
`blocking_score`, `sharpness_consistency`, `quality_tier`.

`quality_tier` is 0 (clean), 1 (degraded), or 2 (heavily degraded),
derived from the other four metrics using the threshold keyword arguments.
All thresholds are keyword-only and can be tuned empirically.

```python
embed_images(
    root: Path | str,
    conn: sqlite3.Connection,
    *,
    model: str = 'both',
    batch_size: int = 8,
    weights_dir: Path | None = None,
    tier_thresholds: dict[str, float] | None = None,
    cancel=None,
    on_progress=None,
) -> tuple[int, int]
```

Scan `root`, compute embeddings and quality metrics for each image, and
upsert into `conn`. Returns `(processed, skipped)`.

- `model`: `'clip'`, `'sscd'`, or `'both'`.
- Images are keyed by `(path, mtime)`. An image is skipped if it already
  has all requested embeddings in the database; only the missing embedding
  is computed on a partial re-run.
- `weights_dir` defaults to `cache_dir/weights`. Model weights are
  downloaded on first use: CLIP via HuggingFace (~605 MB), SSCD from
  `dl.fbaipublicfiles.com` (~90 MB). For HuggingFace downloads, an optional
  `HF_TOKEN` is loaded from `~/etc/imhandler-keys.json` (key `"HF_TOKEN"`)
  or the `HF_TOKEN` environment variable if set.
- `tier_thresholds` is forwarded to `compute_quality_metrics` as keyword
  arguments; pass `None` to use defaults.
- `cancel`: optional object with `.is_set() -> bool`; checked before each
  batch. A `_CancelToken` combining a `threading.Event` and a flag file is
  used by `djview` to support cross-worker cancellation.
- `on_progress`: optional callable `(pct: int, dir_label: str) -> None`;
  called at batch boundaries with completion percentage and current directory.
- Safe to interrupt with ^C: each batch is committed atomically and will be
  skipped on the next run.

```python
find_similar(
    conn: sqlite3.Connection,
    path: Path | str,
    model: str,
    *,
    n: int = 8,
) -> tuple[Row | None, list[dict]]
```

Find the `n` most similar images in the same directory as `path` using
cosine similarity of stored embeddings. Returns `(target_row, neighbors)`:

- `target_row` — the `Images` row for `path` (columns: embedding blob,
  `width`, `height`), or `None` if no embedding exists for this image.
- `neighbors` — list of dicts with keys `path`, `similarity` (float,
  rounded to 3 dp), `width`, `height`, ordered by descending similarity.
  Excludes `path` itself and is restricted to the immediate directory
  (no subdirectories).

### Models

**CLIP ViT-B/32** (`open_clip`, pretrained `'openai'`): general-purpose
image-text embedding. 512-dim L2-normalised output. Good for finding
images of the same subject regardless of framing.

**SSCD disc_mixup** (TorchScript checkpoint from Facebook Research): trained
specifically for copy detection. 512-dim L2-normalised output. More
sensitive to near-duplicates with minor edits (crop, colour grade, etc.)
than CLIP.

---

## `imhandler.clusterer` — Similarity clustering

```python
from imhandler.clusterer import cluster_images
```

```python
cluster_images(
    conn: sqlite3.Connection,
    *,
    threshold: float = 0.85,
    model: str = 'clip',
) -> int
```

Load L2-normalised embeddings from `conn`, compute the full pairwise cosine
similarity matrix (numpy dot product), threshold to an adjacency matrix,
extract connected components (scipy sparse graph), discard singletons, rank
members by quality (quality_tier ascending, laplacian_score descending), and
write results to the `Clusters` and `ClusterMembership` tables.

Any existing clusters with the same `threshold` and `model` are replaced
before writing new results. Clusters from other threshold/model combinations
are left intact.

Returns the number of clusters written.

---

## `imhandler.djview` — Django view set

```python
from imhandler.djview import ImageHandlerViewSet
```

`ImageHandlerViewSet` is a class whose methods are Django view callables.
The deployed Django front ends normally use the shared
`imhandler.djview.views` and `imhandler.djview.urls` modules rather
than carrying local wrapper apps. The URL namespace is `image_handler`.

```python
ImageHandlerViewSet(
    *,
    base_nav,
    nav=None,
    nav_suffix=None,
    nav_rel=None,
    base_nav_rel=None,
    index_specs_url=None,
)
```

- `base_nav`: navigation dict/list used by the base template (host-app specific).
- `nav`: right-side navbar items.
- `nav_suffix`: right-side navbar items appended after `nav`.
- `nav_rel`: relative-path variant of `nav`.
- `base_nav_rel`: relative-path variant of `base_nav`; defaults to `base_nav`.
- `index_specs_url`: optional specs link for the index page.

### Views

All media paths are passed as `?path=<absolute-path>` query parameters.

| Method | HTTP | Query params | Description |
|--------|------|--------------|-------------|
| `index` | GET | — | Root page with section links |
| `browse` | GET | `album=<rel>`, `sort=name\|mtime\|size` | Album tree + image grid |
| `similarity_browse` | GET | `album=<rel>`, `sort=` | Same as browse; marks embedded images; shows embed button |
| `semantic_search` | GET | `q=`, `n=` | CLIP text-to-image search across all embedded images; returns the first `n` thumbnail matches |
| `compare` | GET | `model=clip\|sscd`, `threshold=0.85` | Re-clusters on each load and shows contact sheets |
| `cluster_detail` | GET | `model=`, `threshold=` | Single cluster; auto-cleans missing members |
| `mark_toggle` | POST | — | Toggle `path` (POST body) in the session deletion list |
| `deletion_list_download` | GET | — | Download `delete.sh`; clears the deletion list |
| `deletion_list_clear` | POST | `next=<url>` | Clear deletion list; redirect to `next` |
| `similar` | GET | `path=`, `model=clip\|sscd` | Most similar images in the same directory |
| `thumb` | GET | `path=`, `size=200` | Serve JPEG thumbnail; `size` clamped to 50–800 |
| `image` | GET | `path=` | Stream full-size original |
| `embed_stream` | GET | `album=<rel>` | SSE stream; runs `embed_images` in a background thread, or across all configured roots for multi-root `album=.` |
| `embed_cancel` | POST | — | Cancel running embed (`album` in POST body); CSRF-exempt |

### Session state

`deletion_list` — `list[str]` of absolute paths marked for deletion.
Maintained across requests; cleared by `deletion_list_download` or
`deletion_list_clear`. Not persisted across server restarts.

### Cancellation

`embed_stream` creates a cancel token combining a `threading.Event` (for
same-worker cancels) and one or more tempfile flags (for cross-worker cancels
under gunicorn). `embed_cancel` sets both. The token is passed to
`embed_images` via its `cancel` parameter. For multi-root `album=.`, the
stream iterates all configured roots and aggregates the counts.

### `deletion_list_download`

Generates a POSIX shell script (`delete.sh`) that removes each marked file
with `rm -- '<path>'`, with embedded single quotes escaped as `'\''`. The
deletion list is cleared after the response is sent.

### Templates

Templates live at `templates/image_handler/<name>.html`. The Django app
label is `imhandler_djview`; add `'imhandler.djview'` to
`INSTALLED_APPS` so Django's template loader finds them. Host projects mount
the shared URL module with:

```python
path('image_handler/', include('imhandler.djview.urls')),
```

The package `AppConfig.ready()` calls
`appconfig.init_variant(settings.IMHANDLER_VARIANT)`, defaulting to `hty7`
when the setting is absent.

---

## Configuration

Paths come from `appconfig.init(ac)`, which extracts values from an `AppConfig`
instance. Call `init()` at frontend startup before using any module that reads
paths.

| Config key | Required by | Effect |
|------------|-------------|--------|
| `image_root` | `cache.image_root`, `scanner.scan` | Default image directory |
| `cache_dir` | `thumbnailer`, `db`, `embedder`, `clusterer` | Root for all generated files |

For `scan(root=None)` and `thumbnailer.purge(root=None)`, the `root`
argument defaults to `appconfig.image_root` via `cache.image_root()`.

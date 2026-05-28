# image_handler

Image gallery, similarity browser, and embedding manager. Browses `image_root`
(from `~/etc/imh.conf.local`) using `hty7.imhandler`.

## View logic

All view logic lives in `hty7.imhandler.djview.ImageHandlerViewSet`. This
Django app is a thin wrapper:

```python
from hty7.imhandler.djview import ImageHandlerViewSet
_vs = ImageHandlerViewSet(base_nav=base_nav, nav_rel=base_nav_rel)
```

## URL → view

| URL | View | Notes |
|-----|------|-------|
| `/image_handler/` | `index` | Landing page with nav links |
| `browse/?album=` | `browse` | Album tree or thumbnail grid |
| `similarity/?album=` | `similarity_browse` | Browse with per-image similarity links |
| `similar/?path=&model=` | `similar` | Nearest-neighbour results for one image |
| `compare/?model=&threshold=` | `compare` | Cluster overview grid |
| `cluster/<id>/?model=&threshold=` | `cluster_detail` | Members of one cluster |
| `thumb/?path=&size=` | `thumb` | Serves generated JPEG thumbnail |
| `image/?path=` | `image` | Serves original image |
| `manage/?album=` | `manage` | Embed trigger for an album |
| `embed-stream/?album=` | `embed_stream` | SSE stream for embed progress |
| `mark-toggle/` | `mark_toggle` | POST: toggle path in deletion list |
| `deletion-list/download/` | `deletion_list_download` | Download delete.sh and clear list |
| `deletion-list/clear/` | `deletion_list_clear` | POST: clear deletion list |

## Configuration

Paths come from `~/etc/imh.conf.local` via `hty7.imhandler.appconfig`.
`ImageHandlerConfig.ready()` calls `appconfig.init('hty7')` at Django startup.

- `image_root` — image directory to browse. Missing/invalid → error page.
- `cache_dir` — required for thumbnails, database, and embeddings.

## Data manipulation

All data access and computation is in `hty7.imhandler` (not here):
- `db.open_db()`, `db.get_clusters()`, `db.get_cluster_members()`,
  `db.get_cluster_member_rows()`, `db.cleanup_missing_members()`,
  `db.get_embedded_paths()` — database queries
- `embedder.embed_images()` — compute and store embeddings
- `embedder.find_similar()` — nearest-neighbour search
- `clusterer.cluster_images()` — cluster by embedding similarity
- `scanner.scan()`, `Album.all_images()` — directory traversal
- `thumbnailer.get_or_create()` — lazy thumbnail generation

# Image Handler Django Views — Implementation Notes

Implementation details for `imhandler.djview`. See
`imhandler-django-man.md` for usage and `imhandler-specs.md` for the API
reference.

---

## Host applications

Two Django projects integrate `djview`:

| Project | Path | Config variant |
|---------|------|----------------|
| `knip` | `~/prj/qat/knip/` | `qat` (`[qat.imhandler.core]`) |
| `llime` | `~/prj/grove/llime/` | `hty7` (`[hty7.imhandler.core]`) |

Both are structured identically: the project installs
`imhandler.djview`, sets `IMHANDLER_VARIANT` for its config section, and
includes the shared URL module at `/image_handler/`. The shared views import
`base.lib.tools` from the host project for navigation.

---

## Integration pattern

The shared `imhandler.djview.views` module constructs a viewset instance
and exposes its methods as module-level view callables:

```python
from imhandler.djview import ImageHandlerViewSet
from base.lib.tools import nav as base_nav, nav_rel as base_nav_rel, specs_nav_item

_nav_suffix = [specs_nav_item('imhandler')]

_vs = ImageHandlerViewSet(
    base_nav=base_nav,
    base_nav_rel=base_nav_rel,
    nav=[],
    nav_suffix=_nav_suffix,
    index_specs_url=_nav_suffix[0]['url'],
)

index                  = _vs.index
browse                 = _vs.browse
similarity_browse      = _vs.similarity_browse
semantic_search        = _vs.semantic_search
compare                = _vs.compare
cluster_detail         = _vs.cluster_detail
mark_toggle            = _vs.mark_toggle
deletion_list_download = _vs.deletion_list_download
deletion_list_clear    = _vs.deletion_list_clear
similar                = _vs.similar
thumb                  = _vs.thumb
image                  = _vs.image
embed_stream           = _vs.embed_stream
embed_cancel           = _vs.embed_cancel   # CSRF-exempt static method
```

The shared `imhandler.djview.urls` module uses the namespace
`image_handler`:

```python
app_name = 'image_handler'

urlpatterns = [
    path('',                         views.index,                  name='index'),
    path('browse/',                  views.browse,                 name='browse'),
    path('similarity/',              views.similarity_browse,      name='similarity_browse'),
    path('semantic/',                views.semantic_search,        name='semantic_search'),
    path('compare/',                 views.compare,                name='compare'),
    path('cluster/<int:cluster_id>/', views.cluster_detail,        name='cluster_detail'),
    path('embed-stream/',            views.embed_stream,           name='embed_stream'),
    path('embed-cancel/',            views.embed_cancel,           name='embed_cancel'),
    path('mark/',                    views.mark_toggle,            name='mark_toggle'),
    path('deletion-list/',           views.deletion_list_download, name='deletion_list_download'),
    path('deletion-list/clear/',     views.deletion_list_clear,    name='deletion_list_clear'),
    path('similar/',                 views.similar,                name='similar'),
    path('thumb/',                   views.thumb,                  name='thumb'),
    path('image/',                   views.image,                  name='image'),
]
```

Both host apps include this under `image_handler/`, so the browse page is
at `/image_handler/browse/`, thumbnails at `/image_handler/thumb/`, etc.

In each host project's `settings.py`:

```python
INSTALLED_APPS = [
    ...,
    'imhandler.djview',
]

IMHANDLER_VARIANT = 'hty7'  # llime; knip uses 'qat'
```

`imhandler.djview.apps.ImageHandlerDjviewConfig.ready()` calls
`appconfig.init_variant(IMHANDLER_VARIANT)`, so the variant difference lives
in configuration rather than in host-local app code.

---

## View details

### `index`

Renders `image_handler/index.html` with three section links (Browse,
Similarity, Compare) as relative URLs.

### `browse` and `similarity_browse`

Both dispatch to `_browse_impl(request, similarity_mode)`.

- `?album=<rel>` selects the album relative to `image_root`; defaults to `.`
  (root).
- `?sort=name|mtime|size` controls image order; defaults to `name`.
- Interior albums render a child-list template; leaf albums render an image
  grid.
- In `similarity_mode`, the grid annotates each image with `has_similar`
  (True if the path has an embedding in the database), and the template
  shows an **Embed** button linked to `embed_stream`.

### `semantic_search`

Reads `?q=<text>` and optional `?n=<int>`.

1. Parses `n` with default `10`, clamped to `1..200`.
2. Calls `find_semantic(conn, query, n=n)`.
3. Renders the first `n` matches as clickable thumbnails, ordered by CLIP
   cosine similarity.
4. Each result links directly to `image`, so clicking a thumbnail opens the
   full-size image.

### `compare`

Called with `?model=clip|sscd&threshold=0.85`. Calls `cluster_images()` on
every page load (fast; typically < 1 s). Fetches all cluster members in a
single query via `get_cluster_member_rows()`. Clusters with > 100 members
are separated into `large_clusters` and rendered at the bottom of the page.

### `cluster_detail`

Path parameter `cluster_id`. Model and threshold come from GET params.

1. Calls `get_cluster_members(conn, cluster_id)`.
2. Calls `cleanup_missing_members(conn, cluster_id)` to remove records for
   files that no longer exist.
3. If `remaining_count <= 1` after cleanup, deletes the cluster and redirects
   to `compare`.
4. Reads `request.session['deletion_list']` to mark already-selected images.

### `mark_toggle`

POST only. Reads `path` from POST body. Toggles the path in
`request.session['deletion_list']`. Returns
`JsonResponse({'marked': bool, 'count': int})` for the JS handler to update
the UI without a page reload.

### `deletion_list_download`

Builds a POSIX shell script:

```sh
#!/bin/sh
rm -- '<path1>'
rm -- '<path2>'
```

Single quotes in paths are escaped with `'\''`. Content-Type is
`text/x-shellscript`. The deletion list is cleared (`[]`) after the response
is built, before it is returned.

### `deletion_list_clear`

POST only. Sets `request.session['deletion_list'] = []`. Redirects to the
`next` POST parameter, or falls back to `image_handler:compare`.

### `similar`

Reads `?path=` (absolute path) and `?model=clip|sscd`. Validates that the
path is under `image_root`. Calls `find_similar(conn, path, model)` and
renders up to 8 results. The focal image and each neighbour have Mark buttons
wired to `mark_toggle`.

### `thumb`

Reads `?path=` and `?size=200` (int, clamped to 50–800). Constructs an
`ImageEntry` from the path and its current mtime, calls `get_or_create()`,
and returns the JPEG bytes with `Cache-Control: max-age=3600`. Returns HTTP
404 on any error.

### `image`

Reads `?path=`. Streams the full-size original using a chunked generator
(64 KB chunks). Sets `Content-Length` and `Cache-Control: max-age=3600`.
Uses `mimetypes.guess_type` for the content type.

### `embed_stream`

GET, returns `text/event-stream`. Reads `?album=<rel>`.

1. Resolves `album` to one concrete directory, or to all configured roots when
   `album=.` and multiple roots are configured.
2. Creates a `threading.Event` and one flag-file path per concrete target
   (SHA-256 of each target path, stored in `tempfile.gettempdir()`).
3. Stores the event in `_active_embeds` keyed by the sorted target list.
4. Starts a background thread that calls `embed_images()` once per target with
   a cancel token wrapping the shared event and flag files, and an
   `on_progress` callback that puts `{'type': 'progress', 'pct': int, 'dir': str}`
   messages on a queue.
5. The main thread reads from the queue with a 2-second timeout and yields
   SSE frames. Keepalive comments (`: keepalive`) are sent on timeout. The
   stream ends on a `done` or `error` message.

SSE message types:

| Type | Fields | Meaning |
|------|--------|---------|
| `start` | `message` | Embedding begun |
| `output` | `message` | Line printed to stdout by embed_images |
| `progress` | `pct`, `dir` | Batch-level progress |
| `done` | `processed`, `skipped` | Finished successfully |
| `error` | `message` | Fatal error |

### `embed_cancel`

POST, CSRF-exempt (clients may be on a different origin or may lack a CSRF
token). Reads `album` from POST body.

1. Sets the `threading.Event` in `_active_embeds` if the job is running in
   this worker.
2. Touches every flag file for the resolved target set so jobs running in
   other gunicorn workers also stop.

---

## Session layout

| Key | Type | Set by | Cleared by |
|-----|------|--------|------------|
| `deletion_list` | `list[str]` | `mark_toggle` | `deletion_list_download`, `deletion_list_clear` |

---

## URL generation

All internal URL construction uses `reverse('image_handler:<name>')`. The
`_url()` helper in the module wraps this. Media paths are always passed as
`?path=<absolute-path>` query parameters using `urlencode({'path': ...})`,
avoiding any path-segment encoding issues.

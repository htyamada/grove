# mediaview — architecture

Django app for browsing a filesystem media hierarchy via the browser.
Master copy lives at `~/prj/grove/lib/mediaview/`; installed into each project
by adding `~/prj/grove/lib` to `sys.path` and setting `MEDIAVIEW_LABEL` in
`settings.py`.

## Config

`~/etc/mediaview.conf` — TOML, loaded once and cached.

```toml
cache_dir = "~/var/mediaview/cache"

[[hty7.roots]]
name = "AI Images"
path = "/srv/cloud/OpahSSD/pictures/ai"

[[qat.roots]]
name = "AI Images"
path = "/srv/ext/qat/pictures/ai/art"
```

`conf.py` reads `MEDIAVIEW_LABEL` from Django settings to select the
correct `[[label.roots]]` section. `roots()` → `{name: Path}`,
`cache_dir()` → `Path`.

## Deploying in a project

In `settings.py`:
```python
import sys, os
sys.path.insert(0, os.path.expanduser('~/prj/grove/lib'))
MEDIAVIEW_LABEL = 'qat'   # or 'hty7', etc.
INSTALLED_APPS = [..., 'mediaview']
```

In `urls.py`:
```python
path('mediaview/', include('mediaview.urls', namespace='mediaview')),
```

In `base/lib/tools.py`:
```python
{'name': 'Media Viewer', 'url': reverse_lazy('mediaview:index')},
```

## URL structure

| Pattern | View | Purpose |
|---------|------|---------|
| `` | `index` | List configured roots as folder tiles |
| `browse/<root>/[<path>/]` | `browse` | Directory listing |
| `thumb/<root>/<path>` | `thumbnail` | 200px JPEG thumbnail |
| `large-thumb/<root>/<path>` | `large_thumbnail` | 600px JPEG thumbnail |
| `file/<root>/<path>` | `serve_file` | Original file (any media type) |
| `info/<root>/<path>` | `info` | JSON: sidecar data or EXIF |
| `dirs/<root>/` | `dirs` | JSON: browsable directory list with writable flags |
| `metadata/` | `save_metadata` | POST — create/update displayed JSON sidecar |
| `delete/` | `delete_file` | POST — delete file + sidecar |
| `move/` | `move_file` | POST — move file + sidecar to another configured root/directory |

All URLs are built with `get_script_prefix()` so they work under any
Apache/nginx script alias.

## Thumbnail cache

`thumbs.py` generates thumbnails on demand via PIL (images) or ffmpeg
(video frame at 1s). Cache lives at `cache_dir/thumbs/<xx>/<hash>-<size>.jpg`
where the hash is SHA-256 of the source file's absolute path. Thumbnails are
regenerated if the source mtime is newer than the cached file.

## Browse view

`browse()` lists a directory, sorts dirs before files, skips dotfiles.
Returns two lists to the template:

- **dirs** — subdirectories rendered as folder tiles (navigate on click)
- **items** — media files with precomputed thumb/file/info URLs

Sidecar files (`<filename>.json`) are read inline and embedded in the
items JSON. They are otherwise hidden from the listing.

Security: every path is resolved with `.resolve()` and checked with
`Path.relative_to(root)` before use.

## Template (`browse.html`)

Extends `base/base.html`. Single template handles both the root index and
all subdirectory levels.

**Directory row** — folder tiles for parent (⬆) and subdirectories (📁),
shown before the media grid. On browse pages (not the root index), each tile
carries a `data-dir-relpath` attribute used as the drag-and-drop target.

**Media grid** — 160×160 lazy-loaded thumbnails. Video items get a play
triangle overlay (CSS `::after`). Each thumbnail is `draggable="true"`.

**Drag and drop** — drag a thumbnail onto any directory tile to move that
file (and its associated sidecar files, if present) into that directory. The
parent (⬆) tile is also a valid target as long as the parent is within the
same root; the index-level parent tile is excluded. Directory tiles highlight
with a blue border while a file is dragged over them. The move is a
`POST /move/` request; the page reloads on success. Drag-and-drop targets
stay within the current root.

**Detail overlay** — click a thumbnail to open a panel showing:
- 300px large thumbnail (click → full-size lightbox or video player)
- Sidecar JSON displayed as a key/value table, or EXIF fetched lazily
  via `GET /info/…` if no sidecar is present
- Metadata button opens a JSON editor and writes custom metadata to the
  displayed `<filename>.json` sidecar via `POST /metadata/`
- Rename button prompts for a filename without an extension and posts to
  `POST /rename/`. Rename stays within the current directory, preserves an
  allowed image extension (`.png`, `.jpg`, `.gif`, or `.tiff`), and renames
  associated sidecars to match the new filename.
- Move button opens a directory navigation dialog backed by `GET /dirs/…`.
  It provides root/parent/refresh controls, breadcrumbs, direct path entry,
  a root selector, a subdirectory navigation selector, and a scrollable
  subdirectory list across all configured roots. Only writable destination
  directories can be selected, and the move uses the same sidecar-aware
  `POST /move/` behavior as drag-and-drop. Destinations remain confined to
  configured roots.
- Delete button (POST to `/delete/`, confirms first, reloads on success)

Keyboard: `←`/`→` to navigate, `Esc` to close any overlay.

## Sidecar convention

`photo.jpg` → displayed metadata sidecar is `photo.jpg.json` (same filename,
`.json` appended). Any valid JSON object is accepted; all top-level keys are
displayed as rows.

File operations also treat files named with the full media filename plus an
extra suffix as associated sidecars, for example `photo.jpg.json` or
`photo.jpg.xmp`. Common stem sidecars `photo.xmp` and `photo.aae` are included
too.

# Unified Media Gallery Specification

## Implementation Status

**Implemented.**

| File | Role |
|------|------|
| `lib/llemon_djview/media.py` | Combined Media app view set: Image Creator, Video Creator, shared gallery, archive, source dirs |
| `lib/llemon_djview/sourcedirs.py` | Source directory browser utilities: validation, thumbnail cache, URL resolution |
| `lib/llemon_djview/imagegen.py` | Django view set for unified media: gallery and archive with type-aware operations |
| `lib/llemon_djview/videogen.py` | Django view set for unified media: gallery and archive with type-aware operations |
| `lib/llemon_djview/storage.py` | Django-owned media storage, sidecar, category, thumbnail, upload, and move/delete helpers |
| `lib/llemon_djview/templates/llemon_image/` | Shared Media gallery/archive/source-dirs templates plus Image Creator |
| `lib/llemon_djview/templates/llemon_video/` | Video Creator and video index templates |

## Overview

The media gallery system is unified across image and video generation. Host
Django front ends expose a single Media app with separate Image Creator and
Video Creator pages. There is no distinction between "image gallery" and
"video gallery" at the storage or organizational level. Gallery and archive
display media from shared directories, and operations are
type-aware: operations available to a specific file depend on its type (image
or video), not which creator generated it.

### Unified Storage Model

| Area | Directory |
|------|-----------|
| Gallery | Shared across image & video: `{LLEMON_GALLERY_DIR}` or `{media_dir}/gallery` |
| Archive | Shared across image & video: `{LLEMON_ARCHIVE_DIR}` or `{media_dir}/archive` |
| Categories DB | Single unified database at `gallery/db/gallery.db` |
| Notes DB | Shared by image & video at `notes_dir/notes.db` |

Media pages always share the same directories and display identical content.
The distinction is only in what operations can be performed based on file type.

### Supported Media Types

Gallery and archive display both image and video files:

| Type | Extensions |
|------|-----------|
| Images | `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` |
| Videos | `.mp4`, `.webm`, `.mov`, `.m4v` |

## Upload Behavior

Both images and videos are accepted for upload. Files must have an image or video extension (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` for images; `.mp4`, `.webm`, `.mov`, `.m4v` for videos). This validation is enforced by the upload handlers.

## Gallery Display

The Media gallery lists image and video files from the same directory:

- Thumbnails cached in `gallery/thumbnails/` (160px) and `gallery/thumbnails_large/` (LARGE_THUMB_SIZE)
- Image thumbnails: `llemon_djview.storage.ensure_thumbnail()`
- Video thumbnails: `llemon_djview.storage.ensure_video_thumbnail()`, frame at 1s or fallback to 0.1s/0s
- File metadata (sidecar JSON) loaded and displayed based on file type

## Category System

Categories are stored in a single unified SQLite database: `gallery/db/gallery.db`.

```sql
CREATE TABLE Category (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)
CREATE TABLE CategoryFile (
    category_id INTEGER NOT NULL REFERENCES Category(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    PRIMARY KEY (category_id, filename)
)
```

The Media gallery accesses the shared category database. Gallery supports
filtering by category; archive does not.

### Category Operations

| Operation | URL | Method |
|-----------|-----|--------|
| List categories | `gallery` (implicit) | GET |
| Create category | `gallery` | POST (`category_action=create`) |
| Delete category | `gallery` | POST (`category_action=delete`) |
| Toggle file in category | `gallery` | POST (`category_action=toggle_file`) |

All operations modify the shared database.

## File Operations (Type-aware)

### File Serving

File serving validates against all media types (`_MEDIA_EXTS`):
- `image_file()` serves gallery files (both image and video types)
- `archive_image_file()` serves archive files (both image and video types)

MIME types are guessed from extension and returned as the response content type.

### Deletion

- `delete_image()` — DELETE from gallery (works on any media type)
- `delete_archive_image()` — DELETE from archive (works on any media type)

### Moving Between Gallery and Archive

- `move_to_archive()` — Move gallery file to archive (works on any media type)
- `move_to_gallery()` — Move archive file to gallery (works on any media type)

Moving updates category assignments automatically.

## Image-specific Operations

These operations are only available for image files:

| Operation | Requirement |
|-----------|-------------|
| Upscale (`upscale`, `upscale_archive`) | File must be an image (checked by filename extension) |
| Edit (`edit_image`, `edit_archive_image`) | File must be an image (checked by filename extension) |

Attempting these operations on video files results in a validation error.

## Video Generation with Image References

Video generation accepts image files as reference images:

| Reference Type | Source |
|----------------|--------|
| Start image | gallery or archive (image files only) |
| End image | gallery or archive (image files only) |
| Reference images (ordered) | gallery or archive (image files only) |

All media URLs are converted to `data:` URLs server-side before sending to the provider API.

## Notes and Tags

Both image and video generation use the shared `hty7.llemon.core.notes_db` schema. When `notes_dir` is configured to the same path for both (default in `llemon.conf`), they access the same `notes.db` file.

Notes and tags are model-scoped (provider:model) and are independent of whether the current request is from an image or video context.

## Source Directories

Source directories are read-only image libraries that can be browsed and
copied into the gallery. They are configured in `llemon.conf` and are
never modified by the application. Input files for generation, upscaling,
and editing must come from the gallery.

### Configuration

Source directories are configured in `llemon.conf` under `[{variant}.llemon.mediagen]`:

```toml
[hty7.llemon.mediagen]
source_dirs = [
    "Photos=~/Pictures",
    "Stock=/data/stock-images",
]
source_thumb_dir = "~/var/hty7/llemon/mediagen/source_thumbs"  # optional
```

Each `source_dirs` entry is a `"nickname=path"` string. The nickname is the
user-visible label shown in the browser; the path is the directory on disk.
Tilde in paths is expanded. `source_thumb_dir` is optional; if absent,
thumbnails default to `{media_dir}/source_thumbs/`.

Both keys are parsed by `mediagen.common.init_common_config()` and exposed
via `mediagen.imagegen.get_source_dirs()` and
`mediagen.imagegen.get_source_thumb_dir()`.

### Browser UI

The source dirs browser (`/media/source-dirs/`) has two modes:

- **List mode** (no `?nick=` parameter): shows all configured source directories as folder icons.
- **Browse mode** (`?nick=<nickname>` and optional `?subdir=<relpath>`): shows the root or a subdirectory of the named source dir with:
  - Breadcrumb navigation
  - Parent directory entry
  - Child subdirectory entries (click to navigate in)
  - Image file thumbnails (click to view, download, copy to gallery)

### Copy to Gallery

Clicking **Copy to Gallery** in the image detail overlay copies the source
file into the gallery directory without modifying the original. The copied
filename matches the source; if a file with that name already exists in the
gallery, a numeric suffix is appended (`stem_1.ext`, `stem_2.ext`, …).

If the source file has a sidecar (a `{stem}.json` file alongside it), the
sidecar is loaded as the starting point for the gallery sidecar. The
following fields are then added or overwritten to record copy provenance:

| Field | Value |
|-------|-------|
| `source` | `"source_dir"` |
| `source_nick` | nickname of the source directory |
| `source_rp` | relative path within the source directory |
| `timestamp` | ISO-8601 UTC timestamp of the copy |
| `files` | `[dest_fname]` — gallery filename of the copy |

**POST** `/media/source-dirs/copy-to-gallery/`

Request body: `{"nick": "<nickname>", "rp": "<relative-path>"}`

Response: `{"file": "<gallery-filename>"}` on success, or `{"error": "..."}` on failure.

### Thumbnail Cache

Thumbnails (160 px) are cached outside the source directories at:

```
{source_thumb_dir}/{nickname}/{subdir…}/thumbnails/{filename}
```

Thumbnails are created on first access (browse navigation).
Originals are never written to or modified.

### Security

- Nicknames are validated against the configured list (exact match).
- Subdirectory paths are validated: no `..`, no leading `/`, no `\\`.
- Filenames must have an image extension and contain no path separators.
- `os.path.realpath` is used to guard against symlink escapes.
- All write operations on source directories are prohibited; only reads and thumbnail creation (in the external cache) are performed.

## Access Paths

Host front ends should expose a single Media app route group:

```
/llemon/media/                                       → Media app index
/llemon/media/image-creator/                         → Image Creator
/llemon/media/video-creator/                         → Video Creator
/llemon/media/gallery/                               → gallery display (both media types)
/llemon/media/archive/                               → archive display (both media types)
/llemon/media/source-dirs/                           → source dir browser
/llemon/media/source-dirs/json/                      → source dir JSON API (browser navigation)
/llemon/media/source-dirs/file/<nick>/<path>         → serve source dir image (read-only)
/llemon/media/source-dirs/thumb/<nick>/<path>        → serve source dir thumbnail (cached)
/llemon/media/source-dirs/copy-to-gallery/           → POST: copy source dir image to gallery
```

Gallery, archive, and file operations use the canonical Media URL
names. Video-specific URL names are reserved for Video Creator endpoints such
as generation, model notes, and model-list refresh.

## Error States

| Condition | Behaviour |
|-----------|-----------|
| Media directory not configured | Gallery/archive show empty with error message |
| Invalid filename | File serving returns 404 |
| File not found | File serving returns 404 |
| Category database error | Category operations return error; gallery still displays without categories |
| Operation on wrong file type | Operation returns error (e.g., upscale on video) |

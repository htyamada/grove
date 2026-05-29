# Unified Media Gallery Specification

## Implementation Status

**Implemented.**

| File | Role |
|------|------|
| `lib/llemon_djview/media.py` | Combined Media app view set: Image Creator, Video Creator, shared gallery, archive, uploads, source dirs |
| `lib/llemon_djview/sourcedirs.py` | Source directory browser utilities: validation, thumbnail cache, URL resolution |
| `lib/llemon_djview/imagegen.py` | Django view set for unified media: gallery, archive, uploads with type-aware operations |
| `lib/llemon_djview/videogen.py` | Django view set for unified media: gallery, archive, uploads with type-aware operations |
| `lib/llemon_djview/templates/llemon_image/` | Shared Media gallery/archive/uploads/source-dirs templates plus Image Creator |
| `lib/llemon_djview/templates/llemon_video/` | Video Creator and video index templates |
| `hty7/llemon/mediagen/imagegen/gallery.py` | Image asset management |
| `hty7/llemon/mediagen/videogen/gallery.py` | Video asset management |

## Overview

The media gallery system is unified across image and video generation. Host
Django front ends expose a single Media app with separate Image Creator and
Video Creator pages. There is no distinction between "image gallery" and
"video gallery" at the storage or organizational level. Gallery, archive, and
uploads display the same media from the same directories, and operations are
type-aware: operations available to a specific file depend on its type (image
or video), not which creator generated it.

### Unified Storage Model

| Area | Directory |
|------|-----------|
| Gallery | Shared across image & video: `{LLEMON_GALLERY_DIR}` or `{media_dir}/gallery` |
| Archive | Shared across image & video: `{LLEMON_ARCHIVE_DIR}` or `{media_dir}/archive` |
| Uploads | Shared across image & video: `{LLEMON_UPLOADS_DIR}` or `{media_dir}/uploads` |
| Categories DB | Single unified database at `gallery/db/gallery.db` |
| Notes DB | Shared by image & video at `notes_dir/notes.db` |

Media pages always share the same directories and display identical content.
The distinction is only in what operations can be performed based on file type.

### Supported Media Types

Gallery, archive, and uploads display both image and video files:

| Type | Extensions |
|------|-----------|
| Images | `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` |
| Videos | `.mp4`, `.webm`, `.mov`, `.m4v` |

## Upload Behavior

Both images and videos are accepted for upload. Files must have an image or video extension (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` for images; `.mp4`, `.webm`, `.mov`, `.m4v` for videos). This validation is enforced by the upload handlers.

## Gallery Display

The Media gallery lists image and video files from the same directory:

- Thumbnails cached in `gallery/thumbnails/` (160px) and `gallery/thumbnails_large/` (LARGE_THUMB_SIZE)
- Image thumbnails: `ensure_thumbnail()` from `imagegen.gallery`
- Video thumbnails: `ensure_video_thumbnail()` from `videogen.gallery`, frame at 1s or fallback to 0.1s/0s
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
- `uploads_image_file()` serves upload files (both image and video types)

MIME types are guessed from extension and returned as the response content type.

### Deletion

- `delete_image()` — DELETE from gallery (works on any media type)
- `delete_archive_image()` — DELETE from archive (works on any media type)
- `delete_uploads_image()` — DELETE from uploads (images only, enforced by upload validation)

### Moving Between Gallery and Archive

- `move_to_archive()` — Move gallery file to archive (works on any media type)
- `move_to_gallery()` — Move archive file to gallery (works on any media type)

Moving updates category assignments automatically.

## Image-specific Operations

These operations are only available for image files:

| Operation | Requirement |
|-----------|-------------|
| Upscale (`upscale`, `upscale_uploads`, `upscale_archive`) | File must be an image (checked by filename extension) |
| Edit (`edit_image`, `edit_uploads_image`, `edit_archive_image`) | File must be an image (checked by filename extension) |

Attempting these operations on video files results in a validation error.

## Video Generation with Image References

Video generation accepts image files as reference images:

| Reference Type | Source |
|----------------|--------|
| Start image | gallery, archive, uploads, or source dirs (image files only) |
| End image | gallery, archive, uploads, or source dirs (image files only) |
| Reference images (ordered) | gallery, archive, uploads, or source dirs (image files only) |

All media URLs are converted to `data:` URLs server-side before sending to the provider API.

## Notes and Tags

Both image and video generation use the shared `hty7.llemon.core.notes_db` schema. When `notes_dir` is configured to the same path for both (default in `llemon.conf`), they access the same `notes.db` file.

Notes and tags are model-scoped (provider:model) and are independent of whether the current request is from an image or video context.

## Source Directories

Source directories are read-only image libraries that supplement the uploads
directory. They are configured in `llemon.conf` and are never modified by
the application.

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
user-visible label shown in the browser and picker; the path is the directory
on disk. Tilde in paths is expanded. `source_thumb_dir` is optional; if
absent, thumbnails default to `{media_dir}/source_thumbs/`.

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
  - Image file thumbnails (click to view, download)

### Image Picker Integration

Source dir images are available directly in the image picker used by the
Image Creator (source image selection) and the Video Creator (start, end,
and reference image selection). The picker shows an **Uploads** tab (default)
and a **Source Dirs** tab. The Source Dirs tab is only shown when
`source_dirs_json_url` is present in the template context, which requires
`source_dirs` to be configured.

Selecting a directory navigates into it; selecting an image closes the picker
and sets the chosen image. Navigation fetches directory listings via the JSON
API endpoint (`/media/source-dirs/json/`) rather than a full page reload.

### Thumbnail Cache

Thumbnails (160 px) are cached outside the source directories at:

```
{source_thumb_dir}/{nickname}/{subdir…}/thumbnails/{filename}
```

Thumbnails are created on first access (browse or picker navigation).
Originals are never written to or modified.

### Reference Image Integration

Source dir image URLs (`/media/source-dirs/file/<nick>/<relpath>`) are
recognised by `_MediaVideoViewSet._local_media_path_for_url()` and converted
to `data:` URLs before being sent to provider APIs, making them usable as
start/end/reference images in video generation without any additional upload
step. This uses Django's `resolve()` to identify the URL by name and namespace.

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
/llemon/media/uploads/                               → uploads display (both media types)
/llemon/media/source-dirs/                           → source dir browser
/llemon/media/source-dirs/json/                      → source dir JSON API (picker navigation)
/llemon/media/source-dirs/file/<nick>/<path>         → serve source dir image (read-only)
/llemon/media/source-dirs/thumb/<nick>/<path>        → serve source dir thumbnail (cached)
```

Gallery, archive, uploads, and file operations use the canonical Media URL
names. Video-specific URL names are reserved for Video Creator endpoints such
as generation, model notes, and model-list refresh.

## Error States

| Condition | Behaviour |
|-----------|-----------|
| Media directory not configured | Gallery/archive/uploads show empty with error message |
| Invalid filename | File serving returns 404 |
| File not found | File serving returns 404 |
| Category database error | Category operations return error; gallery still displays without categories |
| Operation on wrong file type | Operation returns error (e.g., upscale on video) |

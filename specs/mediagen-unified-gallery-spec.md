# Unified Media Gallery Specification

## Implementation Status

**Implemented.**

| File | Role |
|------|------|
| `lib/llemon_djview/media.py` | Combined Media app view set: Image Creator, Video Creator, shared gallery, archive, source dirs |
| `lib/llemon_djview/sourcedirs.py` | Source directory browser utilities: validation, thumbnail cache, URL resolution |
| `lib/llemon_djview/imagegen.py` | Django view set for unified media: gallery and archive with type-aware operations |
| `lib/llemon_djview/videogen.py` | Django view set for unified media: video creator |
| `lib/llemon_djview/base_viewset.py` | Base class with shared directory helpers, path validation, and thumbnail utilities |
| `lib/llemon_djview/storage.py` | Django-owned media storage, sidecar, category, thumbnail, upload, and move/delete helpers |
| `lib/llemon_djview/templates/llemon_image/` | Shared gallery/archive/source-dirs templates plus Image Creator |
| `lib/llemon_djview/templates/llemon_video/` | Video Creator template |

## Overview

The media gallery is unified across image and video generation. The host
Django front end exposes a single Media app with separate Image Creator and
Video Creator pages. There is no distinction between "image gallery" and
"video gallery" at the storage or organizational level: gallery and archive
display both media types from shared directories, and operations are
type-aware based on file extension.

### Gallery Philosophy

The gallery has two levels of organisation: the **root gallery** (flat list
of files) and **project galleries** (named subdirectories). This two-level
model is intentional and is the central organisational idea:

- Files not belonging to any specific project live directly in the root
  gallery directory.
- Files belonging to a project live inside a named subdirectory of the root
  gallery. A project subdirectory may itself contain further subdirectories,
  allowing multiple levels of nesting.
- When a project gallery is active, it **takes over completely**: all nav
  links, all creator launches, all tool operations (Upload, Generate,
  Archive, Delete, Reload) target that project. The page title reflects the
  active project. The only way to leave the project context is to navigate
  explicitly to the root gallery or archive.
- The root gallery and any project gallery are browsed through the same
  gallery view; the `?subdir=` query parameter selects the active project.

This model keeps project work isolated without requiring separate apps,
namespaces, or database schemas.

### Unified Storage Model

| Area | Directory |
|------|-----------|
| Gallery (root) | `{LLEMON_GALLERY_DIR}` or `{media_dir}/gallery` |
| Gallery project | `{gallery_dir}/{project_path}/` |
| Archive | `{LLEMON_ARCHIVE_DIR}` or `{media_dir}/archive` |
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

---

## Project Galleries

### What a Project Is

A project is any subdirectory inside the gallery directory that is not a
reserved name (`thumbnails`, `thumbnails_large`, `db`) and does not start
with `.`. Projects can be nested to any depth. The path from the gallery root
to the project is the **subdir**, e.g. `vacation` or `vacation/day2`. There is
no project registry or database; presence of a directory is sufficient.

### Creating a Project

The gallery page shows a **New Project** button (adjacent to the Upload
button). Clicking it reveals an inline form where the user types a name. The
form sends:

```
POST /media/gallery/create-project/
Content-Type: application/json

{"name": "vacation", "subdir": ""}
```

`subdir` is the parent project if creating a nested project, or empty string
for a top-level project. The server calls `os.makedirs(new_dir, exist_ok=False)`
and returns `{"created": "vacation"}` or `{"error": "..."}`.

### Directory Layout

```
gallery/
  thumbnails/                 ← root gallery small thumbs (160 px)
  thumbnails_large/           ← root gallery large thumbs (600 px)
  db/                         ← SQLite category database
  some-image.png
  some-image.json             ← sidecar metadata
  vacation/                   ← project directory
    thumbnails/               ← project small thumbs
    thumbnails_large/         ← project large thumbs
    beach.png
    beach.json
    day2/                     ← nested sub-project
      thumbnails/
      thumbnails_large/
      photo.jpg
```

Thumbnail directories (`thumbnails/`, `thumbnails_large/`) are created on
demand inside each directory that needs them. Reserved directory names are
never offered as navigation targets.

### Navigating Projects

The gallery view reads `?subdir=<path>` from the URL. When absent, the root
gallery is displayed. When present, the specified subdirectory is opened.

The gallery lists:
- A **parent folder tile** (↑) that links to the parent directory (or root).
- **Subdirectory folder tiles** for each child project (click to descend).
- **Media thumbnails** for files directly inside the current directory.

Folder tiles are drag targets: files can be dropped onto them to move
the file into that directory (see [Drag-and-Drop](#drag-and-drop)).

The breadcrumb at the top of the page shows the path to the current
project and each segment is a link back to that level.

### Sticky Project Context

When a project gallery is active, the project context is carried forward to
every action launched from that page:

| Action | How context is carried |
|--------|----------------------|
| Navigate to Image Creator | `?output_subdir=<subdir>` added to URL |
| Navigate to Video Creator | `?output_subdir=<subdir>` added to URL |
| "Reload" button on a file | Same creator opened with `?output_subdir=<subdir>` |
| Upload button | `subdir` field sent in FormData POST |
| Generate (image or video) | `output_subdir` sent in JSON POST body |
| Delete | `subdir` field sent in JSON POST body |
| Archive | `subdir` field sent in JSON POST body |

Within a creator (Image Creator or Video Creator), when `output_subdir` is
set:
- The page H1 title shows `<site>: <page title> / <subdir>`.
- A green banner below the H1 reads "Saving to project: **<subdir>**".
- The "Gallery" nav link points back to the project gallery (`gallery?subdir=<subdir>`).
- The creator's own nav link preserves the project (`image_creator?output_subdir=<subdir>`).
- All generated files are saved to the project directory.
- Large thumbnails are generated in the project's own `thumbnails_large/`.
- The "Archive" nav link always points to the root archive (archive is not project-scoped).

The only exit from project context is deliberately clicking "Gallery" (which
returns to the project gallery) or manually navigating to the root.

### Page Titles

All project-aware pages override the base `{% block heading %}` to append
the active project path:

- Gallery at root: `Site: LLemon Image Gallery`
- Gallery in project: `Site: LLemon Image Gallery / vacation`
- Image Creator in project: `Site: LLemon Image Creator / vacation`
- Video Creator in project: `Site: LLemon Video Creator / vacation`

### Category System

Categories are only available in the **root gallery**. When a project
subdirectory is active, the category controls (create, delete, filter) are
hidden and category operations are disabled. This keeps the category database
simple: filenames in the categories table are bare names with no path prefix,
valid only for root gallery files.

---

## Drag-and-Drop File Moving

Files can be dragged between the root gallery and any project, or between
projects, using HTML5 drag-and-drop.

- Every media thumbnail tile has `draggable="true"` and an `ondragstart`
  handler that records the dragged item's index.
- Every folder tile (parent `..` and child project dirs) has `ondragover`
  and `ondrop` handlers.
- Dropping a file onto a folder tile sends:

```
POST /media/gallery/project-move/
Content-Type: application/json

{"filename": "beach.png", "from_subdir": "vacation", "to_subdir": ""}
```

`from_subdir` or `to_subdir` may be an empty string to indicate the root
gallery. The move is performed by `move_image_asset()` or `move_video_asset()`
from `storage.py`, which atomically moves the file and its sidecar, and
migrates thumbnails from the source thumbnail directories to the destination
thumbnail directories.

---

## URL Patterns

### Root Gallery File Serving

| Pattern | View | Purpose |
|---------|------|---------|
| `media/gallery/image/<str:filename>` | `image_file` | Serve root gallery file |
| `media/gallery/thumb/<str:filename>` | `thumbnail` | 160 px thumb, root gallery |
| `media/gallery/large-thumb/<str:filename>` | `large_thumbnail` | 600 px thumb, root gallery |

### Project File Serving

Project files use `<path:subpath>` to preserve slashes in the URL:

| Pattern | View | Purpose |
|---------|------|---------|
| `media/gallery/project-file/<path:subpath>` | `gallery_project_file` | Serve project file |
| `media/gallery/project-thumb/<path:subpath>` | `gallery_project_thumb` | 160 px thumb, project |
| `media/gallery/project-thumb-large/<path:subpath>` | `gallery_project_large_thumb` | 600 px thumb, project |

`subpath` has the form `<subdir>/<filename>`, e.g. `vacation/beach.png` or
`vacation/day2/photo.jpg`. The view splits on the last `/` to recover `subdir`
and `filename`, then validates both.

### Mutation Endpoints

| Pattern | View | Method | Purpose |
|---------|------|--------|---------|
| `media/gallery/create-project/` | `gallery_create_project` | POST | Create project directory |
| `media/gallery/project-move/` | `gallery_project_move` | POST | Move file within gallery |
| `media/gallery/upload/` | `upload` | POST | Upload files (multipart) |
| `media/gallery/delete/` | `delete_image` | POST | Delete file from gallery |
| `media/gallery/move-to-archive/` | `move_to_archive` | POST | Move file to archive |

### Archive

| Pattern | Purpose |
|---------|---------|
| `media/archive/image/<str:filename>` | Serve archive file |
| `media/archive/thumb/<str:filename>` | 160 px thumb, archive |
| `media/archive/large-thumb/<str:filename>` | 600 px thumb, archive |
| `media/archive/delete/` | Delete from archive |
| `media/archive/move-to-gallery/` | Move from archive to root gallery |

### Source Dirs

| Pattern | Purpose |
|---------|---------|
| `media/source-dirs/` | Source dir browser (list and browse modes) |
| `media/source-dirs/json/` | JSON API for browser navigation |
| `media/source-dirs/file/<nick>/<path>` | Serve source dir image (read-only) |
| `media/source-dirs/thumb/<nick>/<path>` | 160 px thumb (cached externally) |
| `media/source-dirs/thumb-large/<nick>/<path>` | 600 px large thumb (cached externally) |
| `media/source-dirs/copy-to-gallery/` | POST: copy source image into root gallery |

---

## Path Validation and Security

All subdir paths accepted by any endpoint go through two layers of
validation before any filesystem access:

1. **`_safe_subdir(raw)`** (in `base_viewset.py`): splits on `/`, rejects
   any empty component, `.`, or `..`. Returns a clean `/`-joined path or
   raises `ValueError`. Backslashes are normalised to `/` first.

2. **`_validated_project_dir(gallery_dir, subdir)`**: joins the gallery
   root with the subdir components, then checks:
   - No path component is in `_RESERVED_GALLERY_DIRS` (`thumbnails`,
     `thumbnails_large`, `db`).
   - `os.path.realpath(project_dir)` starts with
     `os.path.realpath(gallery_dir) + os.sep` (symlink-escape guard).
   - The resulting path is an existing directory.

   Returns the absolute project path, or `None` if any check fails. Callers
   that receive `None` return HTTP 404 or a JSON error immediately.

Filenames are separately validated by `_safe_filename` (media extension
check, no `/`, no leading `.`) or `_safe_image_name` (images only).

---

## Thumbnail Caching

Every directory that holds media — root gallery, project directories, and
archive — has its own `thumbnails/` (160 px) and `thumbnails_large/` (600 px)
subdirectories. These are created on demand.

`_thumb_dir(media_dir='')` and `_large_thumb_dir(media_dir='')` in
`MediaGenViewSetBase` accept an optional directory argument. When called with
a project directory they return that project's thumbnail directories;
when called with no argument they return the root gallery's thumbnail
directories.

Thumbnails are generated at browse time (gallery page load) and at generate
time (after an image or video is created). For video files the thumbnail is
extracted from the video stream (at 1 s, falling back to 0.1 s and 0 s).

When a file is moved between directories (drag-and-drop or archive/unarchive),
`move_image_asset()` / `move_video_asset()` in `storage.py` migrate existing
thumbnails from the source thumbnail directories to the destination thumbnail
directories.

---

## Upload Behavior

Files may be uploaded to the root gallery or to any project directory.

**POST** `/media/gallery/upload/`  
Content-Type: `multipart/form-data`

| Field | Required | Description |
|-------|----------|-------------|
| `images` | yes (one or more) | File(s) to upload |
| `subdir` | no | Project subdir to save into; empty = root gallery |

Files must have a recognised image or video extension. Each uploaded file
gets a sidecar JSON created with `source: "upload"` and an ISO-8601
timestamp. The response is `{"files": [...], "errors": [...]}`.

---

## Generation Output

Both Image Creator and Video Creator accept an `output_subdir` field in
their generate POST bodies. When set, generated files are saved to the
project directory instead of the root gallery, and thumbnails are created
in the project's thumbnail directories.

The generation response returns file URLs appropriate for the destination:
project files use the `gallery_project_file` URL pattern; root gallery files
use the standard `image_file` / `video_file` pattern.

---

## Video Creator: Source Images from Project

When the Video Creator is opened with `?output_subdir=<subdir>`, the image
picker panel prepends images from the project directory before the root
gallery images. This means the most relevant source frames for the active
project appear at the top of the picker.

Input images selected from the picker are resolved back to filesystem paths
using Django's `resolve()` on the URL: both the standard `image_file` route
and the `gallery_project_file` route are recognised and mapped to their
respective filesystem paths before being converted to `data:` URLs for the
provider API.

---

## File Operations (Type-aware)

### Deletion

`DELETE` from gallery (POST to `delete_image`). Request body:

```json
{"filename": "beach.png", "subdir": "vacation"}
```

`subdir` is optional; empty string or absent means root gallery. The server
resolves and validates the project directory before deleting.

### Moving to Archive

`POST /media/gallery/move-to-archive/`. Request body:

```json
{"filename": "beach.png", "subdir": "vacation"}
```

Files archived from a project are moved to the flat root archive directory
(archive has no project concept). Thumbnails are migrated.

### Moving to Gallery (Unarchive)

`POST /media/archive/move-to-gallery/`. Request body:

```json
{"filename": "beach.png"}
```

Always restores to the root gallery (no subdir support on this direction).

### Image-specific Operations

These are only available for image files:

| Operation | Requirement |
|-----------|-------------|
| Upscale | Image extension (checked by filename) |
| Edit | Image extension (checked by filename) |

Attempting these on video files results in a validation error.

---

## Detail Panel

Clicking any thumbnail in the gallery opens the detail panel. When the
selected file belongs to a project, the panel shows a small "Project:
vacation" line below the filename. All action buttons in the detail panel
(Reload, Archive, Delete) carry the file's `subdir` in their requests
so they operate on the correct directory.

---

## Category System

Categories are stored in a single unified SQLite database at
`gallery/db/gallery.db`. They apply only to root gallery files.

```sql
CREATE TABLE Category (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)
CREATE TABLE CategoryFile (
    category_id INTEGER NOT NULL REFERENCES Category(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    PRIMARY KEY (category_id, filename)
)
```

| Operation | URL | Method |
|-----------|-----|--------|
| Create category | `gallery` | POST (`category_action=create`) |
| Delete category | `gallery` | POST (`category_action=delete`) |
| Toggle file in category | `gallery` | POST (`category_action=toggle_file`) |
| Filter by category | `gallery?category=<id>` | GET |

Category controls are suppressed entirely when a project subdirectory is
active.

---

## Source Directories

Source directories are read-only image libraries that can be browsed and
copied into the gallery. They are configured in `llemon.conf` and are
never modified by the application. Input files for generation, upscaling,
and editing must come from the gallery.

### Configuration

Source directories are configured in `llemon.conf` under
`[{variant}.llemon.mediagen]`:

```toml
[hty7.llemon.mediagen]
source_dirs = [
    "Photos=~/Pictures",
    "Stock=/data/stock-images",
]
source_thumb_dir = "~/var/hty7/llemon/mediagen/source_thumbs"  # optional
```

Each `source_dirs` entry is a `"nickname=path"` string. `source_thumb_dir`
is optional; if absent, thumbnails default to `{media_dir}/source_thumbs/`.

### Browser UI

The source dirs browser (`/media/source-dirs/`) has two modes:

- **List mode** (no `?nick=` parameter): shows all configured source
  directories as folder icons.
- **Browse mode** (`?nick=<nickname>` and optional `?subdir=<relpath>`):
  shows the root or a subdirectory of the named source dir with breadcrumb
  navigation, parent/child directory entries, and image file thumbnails
  (click to view full-size in an overlay, download, or copy to gallery).

### Copy to Gallery

Clicking **Copy to Gallery** copies the source file into the root gallery
directory without modifying the original. If a file with that name already
exists, a numeric suffix is appended (`stem_1.ext`, `stem_2.ext`, …).

If the source file has a sidecar, it is used as the starting point for the
gallery sidecar; the following fields are added or overwritten:

| Field | Value |
|-------|-------|
| `source` | `"source_dir"` |
| `source_nick` | nickname of the source directory |
| `source_rp` | relative path within the source directory |
| `timestamp` | ISO-8601 UTC timestamp of the copy |
| `files` | `[dest_fname]` |

**POST** `/media/source-dirs/copy-to-gallery/`  
Body: `{"nick": "<nickname>", "rp": "<relative-path>"}`  
Response: `{"file": "<gallery-filename>"}` or `{"error": "..."}`

### Thumbnail Cache

Thumbnails (160 px and 600 px) are cached outside the source directories at:

```
{source_thumb_dir}/{nickname}/{subdir…}/thumbnails/{filename}
{source_thumb_dir}/{nickname}/{subdir…}/thumbnails_large/{filename}
```

Thumbnails are created on first access. Originals are never written to.

### Security

- Nicknames are validated against the configured list (exact match).
- Subdirectory paths go through the same `..`-rejection as gallery paths.
- Filenames must have an image extension and contain no path separators.
- `os.path.realpath` guards against symlink escapes.
- All write operations on source directory trees are prohibited.

---

## Error States

| Condition | Behaviour |
|-----------|-----------|
| Media directory not configured | Gallery/archive show empty with error message |
| Invalid or missing project directory | 404 |
| Invalid filename | File serving returns 404 |
| File not found | File serving returns 404 |
| Category database error | Category operations return error; gallery still displays without categories |
| Operation on wrong file type | Operation returns JSON error |
| Project name is a reserved word | `gallery_create_project` returns 400 |
| Drag-drop to same directory | `gallery_project_move` returns 400 |

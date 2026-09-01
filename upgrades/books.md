# TASK 2: Directory-Based Document Previewer

Add a new reusable Django app for browsing and previewing a configured collection
of documents organized by directory. This is separate from the imhandler
blacklist and should be implemented and reviewed independently. The goal is selection
of files for access via downloads or a directory of active items for offline sync.

## 1 — Requirements

### 1.1 Collection and formats

Support PDF (`.pdf`), EPUB (`.epub`), Comic Book ZIP (`.cbz`), Markdown
(`.md`), and plain text (`.txt`). The directory tree is the collection
hierarchy. Show directories and supported documents in stable name order with
breadcrumbs and a collection-root link. Hide unsupported files, dotfiles,
metadata files, and symlinks outside the configured root.

Configure:

```python
DOCUMENT_VIEWER_ROOT = Path('~/Documents/Library').expanduser()
DOCUMENT_VIEWER_ACTIVE_DIR = Path('~/Documents/Reader').expanduser()
```

The collection may be read-only. The active directory is a separate writable
staging area used by an offline reader.

### 1.2 Covers and thumbnails

Every document has a cover tile:

- EPUB: find the declared cover through EPUB 2 metadata or the EPUB 3
  `cover-image` manifest property.
- CBZ: use the first suitable image in natural filename order.
- PDF: render the first page when an optional PDF renderer is available.
- Markdown and text: generate a generic cover with file type and a safely
  truncated title.
- On extraction, decoding, or rendering failure, return a generic cover for
  that format. Missing covers must never break directory browsing.

Cache thumbnails outside the collection. Cache identity includes canonical
path, source mtime and size, extractor version, and requested dimensions.
Bound file size, image size, decompression, page count, and processing time.
Treat EPUB and CBZ as untrusted ZIP input: reject traversal, encrypted entries,
and decompression bombs, and never extract archives into the collection.

### 1.3 Viewing and download

- PDF opens through a range-capable content endpoint in the browser PDF viewer.
- EPUB uses a browser reader whose resources come through validated app URLs;
  scripts and active book content are disabled.
- CBZ has a simple naturally sorted page reader with next/previous and
  fit-width/fit-page controls.
- Markdown renders to sanitized HTML with unsafe raw HTML and links disabled.
- Text renders as escaped UTF-8 in a readable `pre`, replacing isolated decode
  errors.

Reader pages show title, relative path, type, size, modification time,
**Download**, and **Add to reader** state.  Downloads preserve original
bytes and filename, use attachment disposition, and share the content
path validation. Emphasis is on previewing, not reading, so only the initial
part of the document needs to be quickly available.

### 1.4 Basic operations

The first version provides only:

- browse directories;
- view or read a document;
- download the original for offline reading;
- add a document link to the active-reader directory;
- remove a link previously created by this app; and
- refresh or invalidate a cached cover.

Source documents cannot be uploaded, edited, moved, renamed, or deleted.

### 1.5 Active-reader links

**Add to reader** creates a symbolic link in
`DOCUMENT_VIEWER_ACTIVE_DIR` pointing to the selected source. This directory
can be watched or synchronized by offline-reader software.

- The source must be a supported regular file under `DOCUMENT_VIEWER_ROOT`.
- The active directory must not be inside the collection root.
- Django writes only in the active directory, never beside source documents.
- Use the source filename. On collision, append a stable readable suffix
  derived from the collection-relative path.
- Creation is atomic and idempotent. Repeated requests return the existing link
  when it targets the same source.
- Removal is permitted only for a symlink registered as app-created and
  resolving to a supported document under the collection root. Never follow or
  remove the target.
- A future version may add explicit copy mode for synchronizers that cannot use
  symlinks. Copy mode is initially out of scope.

Maintain an app-controlled manifest mapping link names to canonical sources.
Update link and manifest under an inter-process lock with atomic writes. If
filesystem and manifest disagree, show a repairable error rather than deleting
an unfamiliar entry.

### 1.6 Authorization and safety

Browsing and reading follow the host's authenticated-user policy. Download and
active-link mutations require explicit authorization; mutations are POST-only
and CSRF-protected.

Resolve collection-relative identifiers through one shared helper. Reject
absolute user input, traversal, NUL bytes, unsupported suffixes, non-regular
files, and symlink escapes. Do not expose absolute archive paths in URLs or
normal page output.

Serve user formats with explicit content types, `nosniff`, a restrictive
content security policy, and safe content disposition. EPUB, CBZ, Markdown,
and text content must not execute document-supplied script in the host origin.

## 2 — Django Design

### 2.1 App structure

Create a reusable app under `lib/documentview/`:

```text
lib/documentview/
  apps.py
  config.py
  paths.py
  covers.py
  archives.py
  active.py
  views.py
  urls.py
  templates/documentview/
  static/documentview/
  tests/
```

Mount it in `llime` first. Keep public names stable so `../qat/knip` can opt
in later without copying implementation.

### 2.2 Routes

| URL | Name | Method | Purpose |
|---|---|---|---|
| `documents/` | `index` | GET | Browse collection root |
| `documents/browse/<path>/` | `browse` | GET | Browse a directory |
| `documents/read/<path>/` | `read` | GET | Format-appropriate reader |
| `documents/content/<path>/` | `content` | GET/HEAD | Validated streaming content |
| `documents/cover/<path>/` | `cover` | GET | Cached or generic cover |
| `documents/download/<path>/` | `download` | GET | Original-file download |
| `documents/active/add/` | `active_add` | POST | Create an offline-reader link |
| `documents/active/remove/` | `active_remove` | POST | Remove an app-owned link |
| `documents/cover/refresh/` | `cover_refresh` | POST | Invalidate one cover |

EPUB and CBZ internal resources use opaque document/resource identifiers or
separately validated subresource routes; never concatenate archive member names
onto filesystem paths.

### 2.3 Implementation choices

Use Python ZIP support for EPUB/CBZ enumeration with strict limits. Choose an
EPUB reader component only after checking its license, browser security model,
and ability to load through Django URLs. The PDF thumbnail renderer is optional:
if absent, use the generic PDF cover. Natural sorting puts `2.jpg` before
`10.jpg`. Log cover errors with relative paths and return a safe fallback.

## 3 — Implementation Steps

### Step 1 — Skeleton and path boundary

Create the app, settings, namespace, resolver, directory scanner, browse
templates, and tests for traversal, symlinks, unsupported files, missing roots,
permission errors, and stable ordering.

### Step 2 — Covers

Implement generic covers, then EPUB, CBZ, and optional PDF extraction. Test
valid, missing, malformed, encrypted, and hostile archive fixtures. Verify that
all extraction failures return a generic cover.

### Step 3 — Readers and download

Add streaming PDF/download responses, sanitized Markdown/text, CBZ navigation,
and the sandboxed EPUB reader/resource layer. Test range requests, headers,
escaping, archive-member validation, and large-file streaming.

### Step 4 — Offline-reader staging

Implement the active-link manifest, collision naming, locked atomic updates,
add/remove endpoints, and UI state. Test idempotency, collisions, broken links,
foreign entries, concurrent requests, and that removal unlinks only the link.

### Step 5 — Integration and documentation

Add the app to llime settings and navigation, run Django checks and focused
tests, and document configuration, formats, optional PDF dependencies, cover
fallbacks, download behavior, active-directory synchronization, security
limits, and backup expectations for the link manifest.

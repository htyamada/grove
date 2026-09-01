# TASK 2: Directory-Based Document Browser and Previewer

Add a new reusable Django app for browsing and lightly previewing a configured
collection of documents organized by directory. 

The app is a **browser and selection tool, not an online reader**. Its purpose is
to make it easy to find a document, inspect enough of it to decide whether it is
wanted, download an original file for local use (for example, import into
Calibre), or make a selected format available in a separate active directory
that can be synchronized to an offline reader.

## 1 — Requirements

### 1.1 Collection, logical documents, and formats

Support PDF (`.pdf`), EPUB (`.epub`), Comic Book ZIP (`.cbz`), Markdown
(`.md`), and plain text (`.txt`). The directory tree is the collection
hierarchy. Show directories and supported documents in stable natural name
order with breadcrumbs and a collection-root link. Hide unsupported files,
dotfiles, metadata files, and symlinks outside the configured root.

Files in the same directory that have the same basename after removing one
supported suffix are **format variants of one logical document**. For example:

```text
Some Book.epub
Some Book.pdf
Some Book.txt
```

is one displayed document with three available formats, not three unrelated
entries.

- Group variants only within the same collection directory; identical basenames
  in different directories remain distinct documents.
- Preserve every underlying file independently. Grouping affects presentation,
  not storage or file identity.
- Show available-format badges or equivalent controls on both cover and title
  views.
- A document detail page shows all available formats and allows the user to
  choose which original to download or add to the active directory.
- The app may choose a preferred format for cover extraction and preview, but
  must not silently substitute that format when the user explicitly downloads
  or activates another variant.
- Preferred preview-format ordering should be configurable or isolated in one
  helper rather than scattered through views/templates.

Configure:

```python
DOCUMENT_VIEWER_ROOT = Path('~/Documents/Library').expanduser()
DOCUMENT_VIEWER_ACTIVE_DIR = Path('~/Documents/Reader').expanduser()
```

The collection may be read-only. The active directory is a separate writable
staging area used by offline-reader software. The application has no need to
modify collection files.

### 1.2 Browse views, covers, and thumbnails

The collection index and directory pages provide two presentations of the same
logical documents:

- **Cover view** (default): a grid of cover tiles suitable for visual browsing.
- **Title view**: a denser title-oriented listing for fast textual scanning.

The selected view does not change document identity, format grouping, or active
state.

Each logical document has one display cover. Prefer an available format that
can provide a useful cover:

- EPUB: find the declared cover through EPUB 2 metadata or the EPUB 3
  `cover-image` manifest property.
- CBZ: use the first suitable image in natural filename order.
- PDF: render the first page when an optional PDF renderer is available.
- Markdown and text: generate a generic cover with file type and a safely
  truncated title.
- If several variants could provide a cover, use one deterministic preference
  order.
- On extraction, decoding, or rendering failure, try an appropriate fallback or
  return a generic cover. Missing or malformed covers must never break browsing.

Cache thumbnails outside the collection. Cache identity includes canonical
source path, source mtime and size, extractor version, and requested dimensions.
Treat EPUB and CBZ as untrusted ZIP input: reject traversal, encrypted entries,
and decompression bombs, and never extract archives into the collection.
Reasonable resource limits should prevent malformed inputs from consuming
unbounded CPU, memory, or temporary storage, but optimization for very large
online documents is not a primary goal.

### 1.3 Fast preview and download

Preview exists only to support browsing and selection. It does **not** need to
provide a satisfactory full-document reading experience. Do not add bookmarks,
reading-position persistence, themes, pagination systems, continuous whole-book
scrolling, annotations, or other ebook-reader features in the first version.

Clicking a logical document opens a detail/preview page showing its title,
relative path, available formats, sizes/modification times as useful, active
state, **Download**, and **Add to reader** controls.

Preview behavior is intentionally limited and format-appropriate:

- **EPUB:** quickly expose the table of contents and a small number of useful
  opening sections. Prefer semantic navigation labels when available (for
  example preface/introduction and first chapter); otherwise use a conservative
  heuristic such as the first few substantial non-navigation spine sections.
  Present the TOC, preface/introduction, and first chapter as separate preview
  sections when they can be identified. Do not attempt continuous whole-book
  rendering.
- **PDF:** provide a fast first-page or first-few-pages preview when rendering is
  available. Full-document browser reading is not required.
- **CBZ:** provide a small initial-page preview sufficient to identify the work.
  A full comic reader with long-session navigation is out of scope.
- **Markdown:** render an initial portion to sanitized HTML with unsafe raw HTML
  and unsafe links disabled.
- **Text:** render an initial portion as escaped UTF-8 in a readable `pre`,
  replacing isolated decode errors.

The preview should favor low latency over completeness. It is acceptable to
stop after a bounded amount of content; the user can download the original for
actual reading.

Downloads preserve the exact original bytes and filename, use attachment
content disposition, and share the normal path-validation logic. Where a
logical document has multiple formats, each format has its own explicit download
action.

### 1.4 Basic operations and explicit non-goals

The first version provides only:

- browse the collection by directory;
- switch between cover and title views;
- inspect grouped format variants for a logical document;
- preview only enough of a document to support selection;
- download any available original format for offline/local reading;
- add a selected format as a link in the active-reader directory;
- remove a link previously created by this app; and
- refresh or invalidate a cached cover.

Source documents cannot be uploaded, edited, moved, renamed, or deleted.
The application is not a general file manager and is not an online ebook or
comic reader.

### 1.5 Active-reader links

**Add to reader** creates a symbolic link in
`DOCUMENT_VIEWER_ACTIVE_DIR` pointing to the explicitly selected source format.
This directory can be watched or synchronized by offline-reader software.

- The source must be a supported regular file under `DOCUMENT_VIEWER_ROOT`.
- The active directory must not be inside the collection root.
- Django writes only in the active directory for active-item operations, never
  beside source documents.
- If a logical document has several formats, active state is per underlying
  format. Activating the EPUB does not implicitly activate or deactivate its PDF
  variant.
- Use the source filename. On collision with a different source, append a stable
  readable suffix derived from the collection-relative path.
- Creation is atomic and idempotent. Repeated requests return the existing link
  when it targets the same source.
- Removal is permitted only for a symlink that is directly inside
  `DOCUMENT_VIEWER_ACTIVE_DIR`, is registered as app-created, and resolves to a
  supported document under the collection root. Never follow or remove the
  target.
- The remove endpoint must not accept or unlink arbitrary filesystem paths. It
  derives the expected active-link path from app-controlled state.
- Active-item deletion is deliberately limited to app-created symlinks in this
  one configured directory. The app has no delete operation for any other file
  or directory that the web-server account may be able to access.
- A future version may add explicit copy mode for synchronizers that cannot use
  symlinks. Copy mode is initially out of scope.

Maintain an app-controlled manifest mapping link names to canonical sources.
Update link and manifest under an inter-process lock with atomic writes. If
filesystem and manifest disagree, show a repairable error rather than deleting
or replacing an unfamiliar entry.

### 1.6 Authorization and safety

Browsing and previewing follow the host's authenticated-user policy. Download
and active-link mutations require explicit authorization; mutations are
POST-only and CSRF-protected.

Resolve collection-relative identifiers through one shared helper. Reject
absolute user input, traversal, NUL bytes, unsupported suffixes, non-regular
files, and symlink escapes. Do not expose absolute archive paths in URLs or
normal page output.

Serve user formats with explicit content types, `nosniff`, a restrictive
content security policy, and safe content disposition. EPUB, CBZ, Markdown,
and text content must not execute document-supplied script in the host origin.

Filesystem permissions are part of the safety model: the collection can remain
read-only to the Django/Apache account. Application-level checks must still
constrain active-link removal to the configured active directory even if that
account has write access to unrelated web-application data elsewhere.

## 2 — Django Design

### 2.1 App structure

Create a reusable app under `lib/documentview/`:

```text
lib/documentview/
  apps.py
  config.py
  paths.py
  documents.py
  covers.py
  previews.py
  archives.py
  active.py
  views.py
  urls.py
  templates/documentview/
  static/documentview/
  tests/
```

`documents.py` (or an equivalent module) owns logical-document grouping and
format-variant selection so basename grouping is not duplicated in views,
templates, cover extraction, or active-link code.

Mount it in `llime` first. Keep public names stable so `../qat/knip` can opt
in later without copying implementation.

### 2.2 Routes

| URL | Name | Method | Purpose |
|---|---|---|---|
| `documents/` | `index` | GET | Browse collection root |
| `documents/browse/<path>/` | `browse` | GET | Browse a directory |
| `documents/view/<path>/` | `view` | GET | Logical-document detail and fast preview |
| `documents/preview/<path>/` | `preview` | GET | Bounded format-specific preview/subresource |
| `documents/cover/<path>/` | `cover` | GET | Cached or generic cover |
| `documents/download/<path>/` | `download` | GET | Download one exact original variant |
| `documents/active/add/` | `active_add` | POST | Create an offline-reader link |
| `documents/active/remove/` | `active_remove` | POST | Remove an app-owned active link |
| `documents/cover/refresh/` | `cover_refresh` | POST | Invalidate one cover |

The exact URL encoding may distinguish a logical-document identifier from a
specific format-variant identifier. Do not rely on an ambiguous basename when a
request operates on a specific underlying file.

EPUB and CBZ internal resources use opaque document/resource identifiers or
separately validated subresource routes; never concatenate archive member names
onto filesystem paths.

### 2.3 Implementation choices

Use Python ZIP support for EPUB/CBZ enumeration with strict safety limits.
Preview code should extract or render only what the current page needs rather
than preparing an entire large document in advance.

An EPUB JavaScript component may be used to render individual selected EPUB
sections, but the app must not depend on continuous whole-book scrolling. Check
its license and browser security model and load EPUB resources only through
validated app URLs. Server-side extraction of metadata, navigation, and section
selection is acceptable and may be preferable for keeping the preview bounded.

The PDF thumbnail/preview renderer is optional: if absent, use the generic PDF
cover and retain download functionality. Natural sorting puts `2.jpg` before
`10.jpg`. Log cover/preview errors with relative paths and return a safe
fallback instead of breaking collection browsing.

## 3 — Implementation Steps

### Step 1 — Skeleton, grouping, and path boundary

Create the app, settings, namespace, shared path resolver, directory scanner,
logical-document/format-variant grouping, cover/title browse templates, and
tests for traversal, symlinks, unsupported files, missing roots, permission
errors, stable ordering, and same-basename multi-format grouping.

Test explicitly that:

- `Book.epub` and `Book.pdf` in one directory appear as one logical document;
- the same basename in another directory remains a different document;
- every format variant remains independently addressable for download and
  activation; and
- unsupported suffixes do not become variants.

### Step 2 — Covers

Implement generic covers, then EPUB, CBZ, and optional PDF extraction. Select one
cover deterministically for a multi-format logical document. Test valid,
missing, malformed, encrypted, and hostile archive fixtures. Verify that all
extraction failures return a usable fallback.

### Step 3 — Fast previews and download

Implement bounded previews: EPUB TOC plus a few useful opening sections,
first-page/few-page PDF preview when available, initial CBZ pages, and bounded
sanitized Markdown/text. Add exact-original downloads for every format variant.

Test escaping, archive-member validation, preview bounds, malformed navigation,
format fallback, and that previewing does not require processing an entire large
document. Do not build or test full-document reading behavior.

### Step 4 — Offline-reader staging

Implement the active-link manifest, collision naming, locked atomic updates,
add/remove endpoints, and per-format UI state. Test idempotency, basename
collisions, multi-format variants, broken links, foreign entries, concurrent
requests, and that removal unlinks only an app-created symlink directly inside
`DOCUMENT_VIEWER_ACTIVE_DIR` and never its target or unrelated writable data.

### Step 5 — Integration and documentation

Add the app to llime settings and navigation, run Django checks and focused
tests, and document configuration, supported formats, logical-document grouping,
cover/title browsing, preview limits, optional PDF dependencies, cover
fallbacks, download behavior, active-directory synchronization, deletion scope,
security limits, and backup expectations for the link manifest.

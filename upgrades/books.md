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
- Format suffixes are recognized case-insensitively, but a logical document is
  expected to have at most one file for each normalized format. If files such
  as `Book.pdf` and `Book.PDF` coexist, flag the logical document as a collection
  error rather than choosing one silently.
- Show available-format badges or equivalent controls on both cover and title
  views.
- A document detail page shows all available formats and allows the user to
  choose which original to download or add to the active directory.
- The app may choose a preferred format for cover extraction and preview, but
  must not silently substitute that format when the user explicitly downloads
  or activates another variant.
- Preferred preview-format ordering should be configurable or isolated in one
  helper rather than scattered through views/templates.
- Format variants are expected to contain the same document in different
  storage representations. A logical-document URL may therefore identify any
  valid variant; the app deterministically chooses a representative using the
  shared preference helper and regroups the files with the same basename.
  Operations on an underlying file—download and activation in particular—must
  still identify the explicitly selected exact variant.

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
Treat EPUB and CBZ as potentially hostile ZIP input: reject traversal,
encrypted entries, and decompression bombs, and never extract archives into the
collection. PDF input is likewise potentially hostile and is passed only to the
bounded external renderer described below. The expected risk from these files
is low, but parsing and rendering boundaries must still fail safely.
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
  A full comic reader with long-session navigation is out of scope. Treat CBZ
  content as untrusted and self-contained; it must not trigger external
  resource loads.
- **Markdown:** render an initial portion to sanitized HTML with unsafe raw HTML
  and unsafe links disabled. Markdown files are locally authored and trusted to
  reference external HTTP(S) images and links, but they still must not execute
  script or unsafe active HTML in the host origin.
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
- Active-item operations write only to the configured active directory and
  app-controlled manifest/lock locations, never beside source documents or
  elsewhere in the collection.
- If a logical document has several formats, active state is per underlying
  format. Activating the EPUB does not implicitly activate or deactivate its PDF
  variant.
- Use the source filename. If that name is already occupied by a different
  source or an unfamiliar entry, reject the add and present the collision to
  the user; do not invent a second filename, overwrite the entry, or adopt it.
- Individual symlink creation and manifest replacement are atomic and performed
  under the shared lock, but the two are not one crash-durable transaction.
  Repeated requests are idempotent when the registered link is present and
  targets the same source.
- Removal is permitted only for a symlink that is directly inside
  `DOCUMENT_VIEWER_ACTIVE_DIR` and is registered as app-created. If its
  registered source no longer validates as a supported document under the
  collection root — for any reason, whether it has disappeared, is now a
  directory, is unreadable, or is no longer a supported file type — the
  removal still proceeds, but the user is shown a clear, specific reason
  (missing / not a file / unreadable / unsupported type) rather than a
  generic failure. Never follow or remove the target.
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
or replacing an unfamiliar entry. Crash durability across the separate manifest
and symlink operations is not required in the first version: an interrupted
operation may leave a mismatch, which must be reported clearly and left for an
explicit retry or reconciliation rather than silently guessed at.

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
EPUB and CBZ previews are treated as untrusted, self-contained content and may
load only validated internal preview resources. Markdown may display external
HTTP(S) images and links because it is locally authored; its HTML is still
sanitized against script and unsafe active content.

Filesystem permissions are part of the safety model: the collection can remain
read-only to the Django/Apache account. Application-level checks must still
constrain active-link removal to the configured active directory even if that
account has write access to unrelated web-application data elsewhere.

The collection hierarchy and its manual maintenance are trusted. Malicious or
concurrent editing of collection directories, filenames, and symlinks is not in
the threat model: the reader user is the same person who maintains the archive.
The only active-content threat boundary is opening and processing CBZ, PDF, and
EPUB file payloads, whose practical risk is considered low. Markdown and plain
text files are locally authored and trusted, while their browser presentation
still follows the sanitization and content-security rules above.

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

Logical-document routes (`view` and `cover`) carry the collection-relative path
of one valid representative variant. The server resolves that exact file and
regroups its same-directory basename variants; because variants are expected to
contain the same document, which valid variant represents the logical page is
not semantically important. Browse links choose the representative
deterministically through the shared format-preference helper. Format-specific
routes (`download`, activation, and any explicitly selected preview) carry the
exact variant path and never rely on a bare or ambiguous basename.

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

Implement the active-link manifest, collision rejection, locked atomic updates,
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

## 4 — Implementation Plan

### 4.0 Environment findings that shape the plan

Checked what's actually installed/available before committing to approaches,
per the repo's own `lib/mediaview` conventions. **Verified from
`/home/yamada/prj/grove`** (this `upgrades/` directory is a subdirectory of
that checkout, not a separate worktree — `llime/`, `lib/mediaview/`, and the
rest of the tree are present one level up and were inspected directly),
  and used as the basis for the choices below:

- `markdown` 3.10.3 and `bleach` 6.3.0 are installed — use them for Markdown
  preview.
- `Pillow` 12.3.0 is installed — required by `covers.py` for generic covers
  and by the decoded-image limits below; not an optional dependency.
- No `ebooklib`, `pypdf`, `PyMuPDF`/`fitz`, or `pdf2image` are installed.
  `pdftoppm` (poppler-utils) and `mutool` (mupdf-tools) are present on
  `$PATH`. Treat PDF rendering as an external-tool integration behind one
  small constrained wrapper (see 4.1), not a Python PDF library dependency.
  **Decision:** standardize on `pdftoppm` only; do not add `mutool` as a
  second code path unless a concrete fixture shows `pdftoppm` can't handle
  it.
- `defusedxml` is installed — use it (not `xml.etree`) for all EPUB
  `container.xml`/OPF/NCX/nav-doc parsing, since EPUB content is untrusted
  ZIP input.
- No natural-sort dependency is installed or needed; a small local
  `natural_sort_key()` covers the `2.jpg` before `10.jpg` requirement.
- Django 6.1 was confirmed by a runtime check (`python3 -c "import django;
  print(django.VERSION)"`) in this environment — **not** from
  `llime/config/settings.py`, whose generated-file header names Django
  `6.0.4`. That header is just `startproject`
  boilerplate, but the discrepancy is worth a quick re-check in the actual
  virtualenv `llime`'s `manage.py` runs with before relying on 6.1-only
  behavior.
- None of `Pillow`, `bleach`, `markdown`, or `defusedxml` appear in a
  repo-wide dependency manifest — none was found at the top level of this
  checkout. They're facts about this host, not yet a documented contract;
  `lib/documentview/README.md` (4.6) must list them as explicit
  prerequisites so a fresh checkout, or `../qat/knip` opting in later,
  doesn't silently depend on packages nobody pinned.
- Cache/manifest placement follows `lib/mediaview`'s existing pattern of
  state under `~/var/<app>/...` (see `~/var/mediaview`,
  `~/etc/mediaview.conf`'s `cache_dir = "~/var/mediaview/cache"`). Unlike
  `mediaview`, this app's root paths are plain Django settings per spec 1.1,
  so no TOML file is introduced; see the settings list in 4.1.
- **`../qat/knip` will not configure a `DOCUMENT_VIEWER_ROOT` at all** — it
  has no document collection to browse, unlike its `mediaview` usage. This
  doesn't change anything about `lib/documentview`'s design: settings are
  already per-host (spec 2.1, "keep public names stable so `../qat/knip`
  can opt in later"), and the natural reading of that is opt-in — a host
  that has no use for the app simply doesn't add it to `INSTALLED_APPS` or
  mount its `urls.py`, the same as it wouldn't wire up any other unused
  reusable app. There's no requirement here to make `documentview` behave
  sensibly with an *installed-but-unconfigured* root (empty index page,
  silently-disabled routes, etc.) — that's scope this plan doesn't need to
  build for qat, since qat isn't expected to mount the app in the first
  place.

### 4.1 Contracts to finalize before writing code

Several details are load-bearing for Step 1 and must be pinned down first:

**Numeric limits — named settings, not magic numbers**, all in `config.py`
with defaults, all referenced by tests instead of restated as literals:

| Setting | Default | Used by |
|---|---|---|
| `DOCUMENT_VIEWER_MAX_ARCHIVE_ENTRIES` | 2000 | `archives.py` |
| `DOCUMENT_VIEWER_MAX_ENTRY_BYTES` | 64 MiB | `archives.py` (per-entry, streamed) |
| `DOCUMENT_VIEWER_MAX_TOTAL_BYTES` | 256 MiB | `archives.py` (per operation, streamed — cumulative across the entries actually read for that cover/preview, not every member of the archive) |
| `DOCUMENT_VIEWER_MAX_COMPRESSION_RATIO` | 100 | `archives.py` (declared uncompressed size vs. compressed size pre-check) |
| `DOCUMENT_VIEWER_MAX_XML_BYTES` | 4 MiB | `covers.py`/`previews.py` EPUB XML parsing |
| `DOCUMENT_VIEWER_MAX_IMAGE_PIXELS` | 40,000,000 | `covers.py`/`previews.py`, checked locally per-image (never assigned to `Image.MAX_IMAGE_PIXELS`) |
| `DOCUMENT_VIEWER_COVER_SIZES` | `{"thumb": (150, 220), "detail": (300, 440)}` | `covers.py`, `cover` view — the only sizes a request may ask for |
| `DOCUMENT_VIEWER_MAX_PREVIEW_SECTIONS` | 3 | `previews.py` EPUB |
| `DOCUMENT_VIEWER_MAX_PREVIEW_BYTES` | 200 KiB | `previews.py` Markdown/text/EPUB section |
| `DOCUMENT_VIEWER_MAX_CBZ_PREVIEW_IMAGES` | 6 | `previews.py` CBZ |
| `DOCUMENT_VIEWER_MAX_PDF_PREVIEW_PAGES` | 3 | `previews.py`/`covers.py` PDF |
| `DOCUMENT_VIEWER_PDF_RENDER_DPI` | 96 | PDF wrapper |
| `DOCUMENT_VIEWER_MAX_PDF_RENDER_DIMENSION` | 2048 | PDF wrapper; maximum width or height passed to `pdftoppm` |
| `DOCUMENT_VIEWER_SUBPROCESS_TIMEOUT` | 10s | PDF wrapper |
| `DOCUMENT_VIEWER_MAX_SYMLINK_HOPS` | 2 | `paths.py`, `resolve_document()`'s final-component symlink resolution only (4.1) |

**Identifier model — three resolvers, not one**, all in `paths.py`:
- `resolve_directory(root, rel_path)` — must exist and be a directory.
- `resolve_document(root, rel_path)` — must exist, be a regular file, and
  have a supported suffix.
- Archive subresources are *not* filesystem paths at all (see 4.4) and carry
  their own integrity check — see the signed-identifier design there.
- **Symlink policy — confirmed, and narrower than the earlier draft**: the
  real collection at `/srv/cloud/store/books-and-text/` (4.2) **only links to
  files, never to directories**, and only to other files within the same
  hierarchy; none should point outside it. Per spec 1.1's literal wording,
  such in-hierarchy file symlinks are followed and shown normally; one that
  resolves outside `DOCUMENT_VIEWER_ROOT` is simply hidden from listings and
  rejected by the resolvers, the same as an unsupported file — not a hard
  error, since none are expected to exist.
  - Because symlinks are file-only, **only the final component of a path can
    ever be a symlink** — every directory component walked on the way there
    is a real directory, never a symlink. This isn't just an optimization to
    assume; it's the confirmed shape of the collection, and it collapses what
    was previously an ambiguous "hops across the whole walk" budget into an
    unambiguous one: symlink resolution is entirely local to resolving the
    last component, never distributed across multiple directory levels.
  - **The collection also has no symlink-to-symlink chains** — a symlink's
    target is always a real file, never another symlink.
    `DOCUMENT_VIEWER_MAX_SYMLINK_HOPS` (settings table above) is a small
    **2**: one hop covers every real case, and the second is margin, not
    headroom for an actually-chained layout. A chain longer than that is
    unexpected content and is fine to reject outright (hidden, same as an
    escaping symlink).
- **Secure-open contract, split by role now that symlinks are known to be
  file-only:**
  - `resolve_directory()` never needs symlink-handling at all. It walks the
    path one component at a time from an
    `os.open(DOCUMENT_VIEWER_ROOT, O_DIRECTORY)` fd, opening each component
    `O_NOFOLLOW` relative to the current `dir_fd`; every one is expected to
    succeed as a real directory. If a directory-position component were ever
    a symlink — contradicting the confirmed collection shape — it's simply
    hidden/rejected like any other unsupported entry, not specially resolved.
  - `resolve_document()` walks its directory components exactly the same
    way (pinned by `dir_fd`, no symlink handling), then resolves only the
    **final** component: `O_NOFOLLOW` first; if that fails with `ELOOP` (the
    file itself is a symlink), `os.readlink()` it, resolve the target
    (absolute or relative to its containing directory) against
    `DOCUMENT_VIEWER_ROOT` — up to `DOCUMENT_VIEWER_MAX_SYMLINK_HOPS` hops —
    until a non-symlink is reached, check that real path against
    `DOCUMENT_VIEWER_ROOT`'s own real path (containment; outside → hidden,
    same as the policy above), and open *that* file `O_NOFOLLOW` relative to
    its real, already-validated parent `dir_fd` so nothing can substitute a
    different file at the last moment.
  - Net effect: every directory component is immune to a swap once passed
    (pinned by `dir_fd`), and the final component is protected by the last
    `O_NOFOLLOW` open. There is theoretically a window between reading the
    final symlink's target and opening it, but concurrent hostile mutation
    of the collection is explicitly outside the threat model: the person
    using the reader is the same person who manually maintains the archive,
    and no other actor is expected to rewrite its symlinks during a request.
    CBZ, PDF, and EPUB file payloads remain the low-risk untrusted inputs:
    archive contents never influence real filesystem paths, and PDF is
    handled only through the constrained renderer (4.3–4.4).
  - `scan_directory()` (4.2) applies the same final-component symlink
    resolution and containment check to file entries only; subdirectory
    entries are recursed into directly as real directories, with no
    symlink-cycle machinery needed, since a directory can't be a symlink in
    this collection.
  - Callers that need bytes (download, preview, cover extraction) read from
    the already-open final descriptor rather than re-opening the path
    later. This remains Linux/POSIX `openat()`-style, matching this
    deployment's platform. The Step 1 TOCTOU test asserts two things: a
    swap of the *final* component still fails closed (the last-hop
    guarantee holds), and a symlink whose target resolves outside the root
    is rejected/hidden rather than followed.

**Authorization contract:** a single settings hook,
`DOCUMENT_VIEWER_AUTHORIZE(request, action) -> bool`, `action` one of
`"browse"` (index/browse/view/preview/cover), `"download"`, or `"mutate"`
(active_add/active_remove/cover_refresh — cover refresh is a mutation and
requires the same check as active-link changes, not the browse check).
Default implementation (used when the setting is unset) is
`request.user.is_authenticated` for every action, overridable per host so
`llime` and `../qat/knip` can each wire their own policy without touching
`lib/documentview`.

**HTTP error behavior:** invalid or malformed identifiers are rejected;
renderer failure on a cover returns the generic cover; and active-link
manifest/filesystem disagreement is presented clearly to the user and logged.
The exact 4xx/5xx distinction for a missing configured root, an unreadable
deployment mount, a missing requested path, a stale subresource identifier, or
a failed preview is an implementation choice rather than a compatibility
contract. Responses must avoid unnecessary path disclosure, and server logs
must contain enough relative-path and exception context to diagnose deployment
and document failures.

**Active-link mismatch handling:** documented in 4.5.

### 4.2 Step 1 — Skeleton, grouping, path boundary, minimal host integration

Host integration occurs in Step 1 since `manage.py check`,
Django tests, and a browsable page all require it:

- `apps.py` — `DocumentViewConfig` (label `documentview`).
- **Deployment value, confirmed**: `llime/config/settings.py` sets
  `DOCUMENT_VIEWER_ROOT = Path('/srv/cloud/store/books-and-text/')` — an
  already-absolute path under the same `/srv/cloud/store/` tree
  `~/etc/mediaview.conf` already points other collections at (4.0), so no
  `~`-expansion applies to it even though `config.py` still runs every
  configured root through the same `Path(...).expanduser().resolve()`
  normalization for consistency with `DOCUMENT_VIEWER_ACTIVE_DIR`, which
  may use `~`. This is the value Step 1's `manage.py check`/browsing exit
  criteria run against.
- `config.py` — the settings table in 4.1, `DOCUMENT_VIEWER_ROOT`,
  `DOCUMENT_VIEWER_ACTIVE_DIR`, `DOCUMENT_VIEWER_CACHE_DIR` (default
  `~/var/documentview/cache`), `DOCUMENT_VIEWER_ACTIVE_MANIFEST` (default
  `~/var/documentview/active_manifest.json`, deliberately outside
  `DOCUMENT_VIEWER_ACTIVE_DIR` so reader-sync software never sees it),
  `DOCUMENT_VIEWER_AUTHORIZE`, and `DOCUMENT_VIEWER_COVER_SIZES`.
  **`AppConfig.ready()` only validates shape**: every
  setting is present and of the right type (`Path`, callable, dict), and
  `DOCUMENT_VIEWER_ACTIVE_DIR` is not a sub-path of `DOCUMENT_VIEWER_ROOT`
  by pure string/`PurePath` comparison — no filesystem access, so `check`,
  migrations, and unrelated management commands never fail just because a
  deployment mount happens to be absent. The equivalent live check (cache
  dir, manifest path, its lock file, and the active dir all resolving
  outside `DOCUMENT_VIEWER_ROOT`, plus each configured root actually
  existing) runs lazily, once per process, the first time a view or
  management command touches the filesystem. Failure produces a clear safe
  response and a diagnostic log entry, not a startup crash. Document that
  `~` expands under the account running `manage.py`/the WSGI process, which
  may differ from the developer's own home directory.
- `paths.py` — `resolve_directory()` (per 4.1, a pure `dir_fd` walk with no
  symlink-handling — directories are never symlinks in this collection) and
  `resolve_document()` (the same directory walk, plus final-component
  symlink resolution up to `DOCUMENT_VIEWER_MAX_SYMLINK_HOPS`, rejecting any
  target outside `DOCUMENT_VIEWER_ROOT`), both rejecting absolute input,
  `..`, and NUL bytes.
- `documents.py` — `SUPPORTED_SUFFIXES` (matched case-insensitively),
  `natural_sort_key()`, `scan_directory(dir)` (hides a file entry whose
  symlink escapes the root; recurses into subdirectory entries directly, no
  symlink handling needed there per 4.1), the single hardcoded
  `FORMAT_PREFERENCE` ordering used by both `representative_variant()` below
  and `cover_for()` (4.3) — spec 1.1 only requires this isolated in one
  helper, not configurable, so it's a plain module constant rather than a
  Django setting; what the order actually is matters less than there being
  exactly one, e.g. `('epub', 'pdf', 'cbz', 'md', 'txt')` — and grouping.
  Grouping rules are explicit:
  - Only the single rightmost supported suffix is ever stripped, so
    `Book.tar.pdf` groups under basename `Book.tar` (`.tar` is not a
    supported suffix and is never itself stripped).
  - The grouping key (basename with suffix stripped) is compared
    byte-for-byte, case-sensitively, with no Unicode normalization — `Book`
    and `book` do **not** group, documented as a deliberate choice to avoid
    surprising cross-case merges on case-insensitive filesystems.
  - Ordering is `natural_sort_key()` first, then the full original filename
    (case-sensitive) as a deterministic tie-break when natural keys compare
    equal.
  - More than one variant with the same normalized suffix is invalid, so
    `Book.pdf` and `Book.PDF` in the same directory produce a visible collection
    error and a log entry; neither is silently selected as "the" PDF variant.
  - `representative_variant(document)` walks the same `FORMAT_PREFERENCE`
    ordering to select any valid variant for logical `view` and `cover`
    URLs — the same one `cover_for()` walks (4.3), not a second,
    independently-tunable ordering. Exact download, activation, and
    explicitly selected preview controls retain the chosen underlying
    variant path.
- `urls.py` / `views.py` — only `index`, `browse`, and `view` are wired now.
  `view` shows title, relative path, and available formats with no
  download/active controls yet (those routes don't exist until the steps
  that implement them); placeholder routes are not added.
- `templates/documentview/` — `index.html`, `browse.html` (cover/title
  toggle, placeholder cover icon), `view.html`, breadcrumbs partial;
  `static/documentview/` minimal CSS.
- `llime/config/settings.py` / `config/urls.py`: add `'documentview'` to
  `INSTALLED_APPS`, the settings from 4.1, and mount `documentview.urls`
  under `documents/` now, not deferred to Step 5.
- `tests/test_paths.py`, `tests/test_documents.py`: traversal, an
  in-hierarchy file symlink resolving and being followed correctly, a file
  symlink escaping the root being hidden/rejected, a symlink chain past
  `DOCUMENT_VIEWER_MAX_SYMLINK_HOPS` being rejected, a directory-position
  entry that unexpectedly is a symlink being hidden rather than followed
  (defensive — the confirmed collection shape never produces this, but
  `resolve_directory()` must still fail closed if it did), unsupported
  files, missing root, permission errors, stable natural
  ordering with the tie-break case, `Book.epub` + `Book.pdf` grouping,
  `Book.tar.pdf` basename handling, same basename in a different directory
  staying distinct, every variant independently addressable, unsupported
  suffixes never becoming variants, duplicate normalized formats such as
  `Book.pdf` + `Book.PDF` producing a collection error, deterministic
  representative selection, and a **TOCTOU test against the
  narrowed secure-open contract**: after resolution has
  validated a document and pinned its real parent directory, replace the
  final component with a symlink pointing outside the root and assert the
  final `O_NOFOLLOW` open still fails closed rather than following it; and
  separately, resolve a document reached through an in-hierarchy symlink
  and confirm it succeeds normally. These two tests match the guarantee
  4.1 actually claims (the last-hop open is race-free; symlink targets are
  containment-checked) rather than a stronger one the design doesn't
  provide.
- Exit criteria: `./manage.py check` clean, `./manage.py test documentview`
  passing, and the collection browsable end-to-end (through the real
  `llime` mount) with placeholder covers.

### 4.3 Step 2 — Covers

- `archives.py` — `safe_zip_open()` enforces the limits table in 4.1.
  **Primary enforcement is on the streamed decompressed output**, since
  that's the only thing Python's `zipfile` actually lets a caller observe
  live. One archive-reader/context object owns a cumulative decompressed-byte
  counter for the complete cover or preview operation; every member read
  through it contributes to that same total, so opening members separately
  cannot reset or bypass `DOCUMENT_VIEWER_MAX_TOTAL_BYTES`. It reads each
  member via `ZipExtFile.read(chunk_size)`, also tracks the current member's
  decompressed bytes and the archive entry count, and aborts mid-read
  past `DOCUMENT_VIEWER_MAX_ENTRY_BYTES`/`_MAX_TOTAL_BYTES` regardless of
  what the central directory claims. `ZipInfo.compress_size` /
  `ZipInfo.file_size` are used only as a **cheap declared-ratio pre-check**
  against `DOCUMENT_VIEWER_MAX_COMPRESSION_RATIO` before opening an entry at
  all — a fast rejection for an obviously hostile declared ratio, not a
  live streamed counter (`zipfile` doesn't expose one; the earlier wording
  here implied it did). Rejects traversal
  member names and encrypted entries outright. Used by `covers.py` and,
  from Step 3, `previews.py`.
- **Shared bounded-image decode helper**, e.g. `_bounded_image_open()` in
  `images.py`, used by every place in `documentview` that decodes an
  arbitrary image byte stream — `covers.py`'s EPUB/CBZ/PDF-raster cover
  extraction *and* `previews.py`'s `cbz_preview()` page decoding alike —
  so the decompression-bomb policy lives in exactly one place instead of
  being restated (and potentially drifting) per call site:
  - **Do not touch the process-global `PIL.Image.MAX_IMAGE_PIXELS`** —
    `lib/documentview` shares its process with other Pillow consumers, so a
    module-level assignment there is a global side effect. Instead:
    `Image.open()` only reads the header, so check
    `img.size` against `DOCUMENT_VIEWER_MAX_IMAGE_PIXELS` *before* calling
    `.load()` or building a thumbnail. `DecompressionBombWarning` may be
    raised by `Image.open()` itself while the header is read, so locally scope
    `warnings.catch_warnings(); warnings.simplefilter('error',
    Image.DecompressionBombWarning)` around `Image.open()`, dimension
    inspection, and the subsequent decode/thumbnail work so an
    over-limit or bomb-like image becomes this helper's own exception —
    caught by the caller and converted to the generic-cover fallback (or,
    for `cbz_preview()`, to skipping that page) — never a process-wide
    setting change.
- `covers.py`:
  - `cover_for(document)` walks `documents.py`'s `FORMAT_PREFERENCE` (4.2) —
    the same ordering `representative_variant()` uses, not a second one.
    Because that ordering is a fixed code constant rather than a runtime
    setting, it can't change without a deploy, which already bumps the
    extractor version below — so it doesn't need its own slot in the cache
    key. **Cache key covers the whole selection, not just the winner** (a
    suffix tuple alone misses changes to unselected variants): a hash of
    the selected source's canonical path + `mtime_ns` + size; a sorted
    tuple of `(relative_path, mtime_ns, size)` for **every** candidate
    variant considered during selection; the requested named size (from
    `DOCUMENT_VIEWER_COVER_SIZES`, 4.1); and the extractor version. A new
    variant, a removed one, or an edited alternate all change the key.
  - EPUB: `container.xml` → OPF (`defusedxml`, `DOCUMENT_VIEWER_MAX_XML_BYTES`
    enforced before parsing) → EPUB3 `cover-image` property, else EPUB2
    `<meta name="cover">`, else `<guide>` reference.
  - CBZ: first image entry in `natural_sort_key()` order.
  - PDF: the shared PDF wrapper from 4.4, first page only, rendered up to
    `DOCUMENT_VIEWER_MAX_PDF_RENDER_DIMENSION` — a safety cap on the raster
    the wrapper produces, decoupled from the requested named box (below).
  - **Fit-and-pad, applied uniformly to whatever raw cover image was
    obtained above, regardless of source format**: scale the raw image,
    preserving aspect ratio, to the largest size that fits entirely within
    the requested named box from `DOCUMENT_VIEWER_COVER_SIZES` (never
    cropped, never stretched to a different aspect ratio), then pad the
    remaining space with a fixed neutral background so the cached/served
    image is always exactly the named box's width × height. This is the one
    place box-fitting happens, so it never needs restating per format: an
    EPUB's embedded cover, a CBZ page image, and a PDF page raster all go
    through the same final step, and no client-supplied dimensions ever
    reach a renderer directly.
- `cover` view (GET, `"browse"` authorization) and `cover_refresh` (POST,
  `"mutate"` authorization — not available to every browsing user).
- `tests/test_covers.py`: valid EPUB2/EPUB3/CBZ/PDF fixtures; missing cover;
  malformed ZIP; encrypted ZIP; a decompression-bomb fixture (both a
  compressed-bytes bomb and a decoded-pixel bomb, to exercise both new
  caps); PDF renderer absent; deterministic preference order; cache
  invalidation when a preferred variant is added/removed; fit-and-pad
  produces an output exactly matching the requested named box for a source
  image wider than the box, taller than the box, and already matching its
  aspect ratio (no cropping or stretching in any case).

### 4.4 Step 3 — Fast previews and download

- **PDF wrapper** (shared by cover and preview rendering): one
  function in `previews.py` (or a small `pdfrender.py`) that runs `pdftoppm`
  via `subprocess.Popen` with a fixed argv list (`shell=False`), in a
  private `tempfile.mkdtemp()` under `DOCUMENT_VIEWER_CACHE_DIR`, capped at
  `DOCUMENT_VIEWER_PDF_RENDER_DPI`, `DOCUMENT_VIEWER_MAX_PDF_PREVIEW_PAGES`,
  and `DOCUMENT_VIEWER_MAX_PDF_RENDER_DIMENSION` (using Poppler scaling so an
  enormous declared page size cannot produce an enormous raster),
  started with `start_new_session=True` so a `DOCUMENT_VIEWER_SUBPROCESS_TIMEOUT`
  timeout can `os.killpg()` the whole process group (poppler's own children
  included), not just the direct child. The wrapper reads the bounded selected
  output into memory before returning, then removes the private temporary
  directory and every generated output in a `finally` path after success,
  renderer failure, timeout, or output-read failure; no response streams from a
  path that cleanup has already removed. PDF bytes are treated as potentially
  hostile despite their low expected risk; they are never interpreted by the
  Django process itself or served inline as active content. The resolver's
  already-open PDF descriptor is passed to the child and addressed through
  `/proc/self/fd/<fd>` with `pass_fds`, so the wrapper does not reopen the
  original pathname; generated images are dimension-checked before full
  decode. The wrapper only ever produces a raster bounded by
  `DOCUMENT_VIEWER_MAX_PDF_RENDER_DIMENSION` — it has no notion of a
  requested cover box. Fitting that raster into one of
  `DOCUMENT_VIEWER_COVER_SIZES`' named boxes is `covers.py`'s job alone (4.3,
  fit-and-pad), keeping the safety cap and the presentation concern
  separate; `previews.py`'s PDF page images pass through unpadded, at their
  natural aspect ratio, since previews aren't fit into a fixed box.
- `previews.py`:
  - `epub_preview()` — TOC + preface/introduction + first chapter, capped at
    `DOCUMENT_VIEWER_MAX_PREVIEW_SECTIONS` / `DOCUMENT_VIEWER_MAX_PREVIEW_BYTES`.
  - `pdf_preview()` / `cbz_preview()` — bounded per the table in 4.1.
    `cbz_preview()` decodes each candidate page through the same
    `_bounded_image_open()` helper `covers.py` uses (4.3), so the
    `DOCUMENT_VIEWER_MAX_IMAGE_PIXELS`/`DecompressionBombWarning` policy
    applies identically to preview pages, not just the CBZ cover; a page
    that fails the check is skipped rather than aborting the whole preview.
    `pdf_preview()`'s images come from the shared PDF wrapper above, which
    is already dimension-bounded by `pdftoppm`'s own capped raster, not a
    second independent Pillow decode.
  - `markdown_preview()` — byte-capped read, then `markdown.markdown()`
    (core extensions only) **followed by `bleach.clean()` as the actual
    enforcement point**: Python-Markdown passes inline raw HTML through
    unchanged by default, so it's `bleach.clean(html, tags=ALLOWED_TAGS,
    attributes=ALLOWED_ATTRS, protocols=['http', 'https'], strip=True)` that
    removes any script/event-handler/raw-HTML the source embedded and
    restricts link/image schemes to `http`/`https` — documented explicitly
    so this isn't mistaken for a redundant second pass.
  - `text_preview()` — bounded read, `errors='replace'`.
- No in-process CPU wall-clock deadline is imposed on ZIP reading, XML
  parsing, sanitization, or image decoding beyond the byte/entry/pixel caps
  above — only the external `pdftoppm` call has a hard
  `DOCUMENT_VIEWER_SUBPROCESS_TIMEOUT`. The size caps are the accepted
  bound on in-process work; the deployment's own request timeout is the outer
  backstop. This matches spec 1.2's framing that optimizing for very large
  or adversarial documents beyond reasonable limits isn't a v1 goal.
- **EPUB HTML containment, with a per-tag policy reconciled against
  Markdown's:** EPUB and
  Markdown preview HTML share one `bleach.clean()` tag/attribute allowlist —
  `<script>`, all `on*` attributes, `<iframe>`, `<object>`, `<embed>`,
  `<form>`, `<svg>`, `<style>`/inline `style=`, and `data:` URLs are always
  stripped — but differ on remote-resource handling, because the two
  inputs carry different trust: a Markdown file is a standalone document
  the user placed in their library, while EPUB XHTML is packaged archive
  content that can already bundle arbitrary assets and has no legitimate
  need to phone home during a bounded preview. CBZ preview images likewise come
  only from validated members of the selected archive and never cause external
  resource loads.
  - **EPUB**: `<img src>` is rewritten to an internal validated
    preview-subresource URL if the referenced path is a real manifest item,
    otherwise dropped; remote `img`/`audio`/`video`/font/CSS resource loads
    are always stripped, never fetched. `<a href>` with an `http`/`https`
    scheme is kept as inert text-with-link (`rel="noopener noreferrer
    nofollow"` added) — a user-initiated navigation, not an automatic
    fetch, so it doesn't carry the same tracking/uncontrolled-request
    concern as an auto-loaded image.
  - **Markdown**: `bleach.clean(html, tags=ALLOWED_TAGS,
    attributes=ALLOWED_ATTRS, protocols=['http', 'https'], strip=True)`
    keeps `http`/`https` `img src` and `a href` as-is — a Markdown doc
    legitimately embedding a remote diagram or linking out is normal, since
    it isn't archive-packaged content — with `rel` hardening on links and
    everything outside `ALLOWED_TAGS`/`ALLOWED_ATTRS` stripped.
  The response CSP (spec 1.6) is defense in depth on top of both allowlists,
  not the only control. EPUB/CBZ preview responses allow images only from the
  host's validated internal resource routes; Markdown preview responses may
  additionally allow HTTP(S) images. This app does not attempt actual
  separate-origin isolation in v1.
- **Archive subresource identifiers, signed** (an encoded
  hash/fingerprint/index has no integrity by itself, since a client
  can alter any of those fields, and a path hash alone isn't reversible
  into the document it names). The design instead relies on the URL
  structure already in section 2.2: `documents/preview/<path>/` carries the
  document's collection-relative path as a real, independently validated
  URL segment (through `resolve_document()`, same as
  `view`/`cover`/`download`) — the subresource id never needs to encode
  *which document*, only *which member of it*. The id itself is
  `django.core.signing.dumps({'i': index, 'fp': f'{mtime_ns}:{size}'},
  salt='documentview.subresource')`, so it's tamper-evident (a modified
  field fails signature verification → 400) rather than merely obscure. On
  each request: `resolve_document()` on the URL's path segment,
  `signing.loads(..., salt=..., max_age=None)` to verify and decode (bad
  signature → 400), compare the decoded fingerprint to the document's
  current `mtime_ns`/size (mismatch → the stale-identifier case, an
  implementation-chosen 409 per 4.1's HTTP error behavior), and
  independently bounds-check `index` against the freshly re-parsed
  spine/manifest before use — defense in depth even though the signature
  already prevents tampering.
- `download` view: `resolve_document()`, `FileResponse` with attachment
  disposition and the original filename, `"download"` authorization; one
  route per format variant.
- `tests/test_previews.py`, `test_download.py`: subresource-id
  escaping/tampering (including a request replaying an id after the
  underlying file's mtime changed → 409), preview bounds enforced against
  the named settings (not restated literals), malformed/missing navigation
  falling back to the spine heuristic, PDF/CBZ preview degrading to a safe
  error or fallback (per 4.1's HTTP error behavior) when the renderer times
  out (download still works), a PDF with an enormous declared page size
  remaining within the configured raster dimension, and a large-fixture
  bounded-I/O test.

### 4.5 Step 4 — Offline-reader staging

**Locked updates and mismatch handling.** Crash durability across the manifest
and symlink operations is deliberately not a v1 requirement. Each individual
manifest replacement and symlink operation is atomic and all related checks
and writes occur under the inter-process lock, but an interrupted operation may
leave the two stores inconsistent. Such disagreement is reported to the user
and logs rather than silently inferred. The manifest is the sole source of
truth for ownership; a bare on-disk symlink is never trusted on its own:
- `add_active(source)`: acquire the lock → compute the deterministic link
  name (collision policy below) → look it up in the manifest.
  - **Already registered to this same source**: idempotent success only when
    the symlink is present and correct. A missing or different link is a
    manifest/filesystem mismatch and is reported rather than guessed at.
  - **Not registered, and nothing on disk at that name**: create the symlink
    and write the manifest using temporary-file plus
    `os.replace()` updates under the lock. If either operation fails, report
    the failure and any resulting mismatch clearly; cross-operation crash
    durability and rollback are deliberately out of scope.
  - **Not registered, but something already exists on disk at that name**:
    an unfamiliar entry. Return 409 (manifest/filesystem disagreement) and
    do **not** create, overwrite, or adopt it — a normal `add_active`
    request never adopts.
  - Release the lock.
- `remove_active(link_name)`: acquire the lock → validate that it is a
  symlink directly inside the active dir and is present in the manifest as
  app-created, using `lstat`/`readlink` immediately before acting. Then
  classify the registered source with `resolve_document()`'s own checks —
  missing (`ENOENT`), present but not a regular file (e.g. now a directory),
  present but unreadable (permission error), present but no longer a
  supported suffix, or valid — and unlink unconditionally in **every** case:
  removal never depends on the source still validating. What differs is only
  the response: a valid source removes normally; any invalid classification
  removes the same way but returns the specific, user-facing reason (missing
  / not a file / unreadable / unsupported type) rather than a generic
  failure or a silent success. Unlink via `dir_fd`-relative operations
  (`os.open(active_dir)`, then `os.unlink(name, dir_fd=fd)`) without
  following the target, update the manifest, and release the lock.
- **Reconciliation management command**
  (`./manage.py documentview_reconcile_active`) reports inconsistencies and
  may explicitly repair app-owned manifest entries when invoked by an operator;
  it is not an automatic crash-durability mechanism:
  - A manifest entry whose symlink is missing is reported. An explicit repair
    may recreate it if the recorded source is still valid or remove the stale
    manifest entry; normal web requests do neither implicitly.
  - A registered symlink whose recorded source no longer validates (missing,
    not a file, unreadable, or unsupported type) is reported with the same
    specific reason `remove_active` would show; explicit reconciliation may
    remove both the broken symlink and its stale manifest entry, matching
    the cleanup a normal remove request performs once a user notices it.
  - A symlink directly inside `DOCUMENT_VIEWER_ACTIVE_DIR` with **no**
    manifest entry is always reported as foreign and **left in place,
    never removed or adopted automatically** — deletion authority for
    unfamiliar entries stays with the operator (spec 1.5). A future
    `--adopt <name>` flag could let an operator explicitly claim one after
    reviewing it; that's out of scope for v1 and not the default path.
- **Collision policy:** the source filename is the only candidate link name.
  If that name is occupied by a different registered source or by an
  unfamiliar filesystem entry, reject the operation and present the collision
  to the user. Repeating an add for the same registered source remains
  idempotent when its symlink is present and correct.
- `active_add` / `active_remove` — POST, `"mutate"` authorization (4.1);
  per-format active-state badges on `view.html` and the cover/title tiles.
- `tests/test_active.py`: same-source idempotent add; a negative test asserting
  a manifest entry with a missing or mismatched symlink produces a clear error
  rather than implicit repair; a **separate, negative test** asserting
  `add_active` returns 409 and makes no changes when an unregistered
  foreign symlink already occupies the computed name; a same-filename collision
  across source directories being rejected with a user-visible error;
  activating one variant leaves siblings untouched;
  remove refuses anything not a direct app-created symlink; remove request
  can't smuggle an arbitrary path; removing a registered link always
  succeeds regardless of source state, exercised across all four invalid
  classifications — source missing, source replaced by a directory, source
  unreadable, source no longer a supported suffix — each asserted to remove
  the symlink and manifest entry while returning its own specific,
  user-facing reason rather than a generic error; concurrent add/remove
  under lock contention leaves manifest and directory consistent;
  `documentview_reconcile_active` reports a manifest entry with a missing
  symlink or a registered symlink whose source no longer validates (any of
  the four classifications above) and supports only explicit operator
  repair, and **reports but does not remove** a foreign unregistered
  symlink (asserting the entry is still present on disk after the command
  runs).

### 4.6 Step 5 — Integration and documentation

With host mounting already done in Step 1, this step is polish and
verification only:

- Navigation link, final `./manage.py check`,
  `python3 -m py_compile` over the new modules, `./manage.py test
  documentview`, and the repo-level
  `python3 -m unittest discover -s tests -t .`.
- `lib/documentview/README.md` (matching `lib/mediaview`'s
  README.md/AGENTS.md pattern): configuration (including
  `DOCUMENT_VIEWER_ROOT = /srv/cloud/store/books-and-text/`, the confirmed
  symlink policy — the collection links only to files, never directories;
  in-hierarchy file symlinks are followed, ones escaping the root are
  hidden — and its hop-count limit, `documents.py`'s single hardcoded
  `FORMAT_PREFERENCE` ordering (not a setting) shared by
  `representative_variant()` and `cover_for()`, the cover fit-and-pad
  behavior (aspect-preserving, padded to the named box, never cropped or
  stretched), the `~`-expansion account
  caveat, and that config validation is lazy rather than at startup — 4.1,
  4.2), the `Pillow`/`bleach`/`markdown`/`defusedxml` prerequisites as
  explicit dependencies (the repo has no top-level requirements manifest
  today, so a new checkout or `../qat/knip` opting in must install these
  itself), supported formats, grouping rules (including the `Book.tar.pdf`
  and case-sensitivity specifics), cover/title browsing and the fixed
  `DOCUMENT_VIEWER_COVER_SIZES` set (no arbitrary requested dimensions),
  preview limits (the settings table) and the explicit non-goal of an
  in-process CPU deadline beyond those size caps and the `pdftoppm`
  subprocess timeout, the `pdftoppm` dependency and its absence behavior,
  cover fallback order, download semantics, active-directory sync model and
  the non-adoption and collision-error behavior on `add_active`, the
  manifest's best-effort mismatch model and `documentview_reconcile_active`
  (including that it reports
  rather than removes foreign entries), deletion scope, security limits
  (archive/image caps, the per-tag EPUB vs. Markdown URL policy), the
  secure-open (`O_NOFOLLOW`/`dir_fd`) contract and its Linux/POSIX scope,
  and backup expectations for the manifest file.
- Confirm `lib/documentview` has no `llime`-specific imports, matching
  spec 2.1's "keep public names stable" — kept as a hygiene check on the
  code, not because `../qat/knip` opt-in is expected any time soon: qat has
  no document collection and won't set `DOCUMENT_VIEWER_ROOT` (4.0), so in
  practice it simply won't mount this app.

### 4.7 Decisions carried forward

- Collection root, confirmed: `DOCUMENT_VIEWER_ROOT =
  /srv/cloud/store/books-and-text/` (4.2). This is the value Step 1's
  `manage.py check` and browsing exit criteria run against.
- Symlink policy, confirmed: the collection links only to files, never
  directories, so only a document's final path component can ever be a
  symlink; in-hierarchy file symlinks are followed (up to
  `DOCUMENT_VIEWER_MAX_SYMLINK_HOPS`), and ones resolving outside
  `DOCUMENT_VIEWER_ROOT` are hidden, not errored, since none are expected
  to exist in the real collection (4.1).
- Format preference, confirmed: one hardcoded `FORMAT_PREFERENCE` ordering
  in `documents.py`, not a Django setting — spec 1.1 only requires it be
  isolated in a single helper, not configurable — shared by
  `representative_variant()` and `cover_for()` rather than two independent
  orderings (4.2, 4.3).
- Cover sizing, confirmed: fit-and-pad — scale preserving aspect ratio to
  the largest size fitting the requested named box, then pad to fill it
  exactly, applied uniformly after obtaining the raw cover image regardless
  of source format; the PDF wrapper's dimension cap is a raster safety
  limit only, separate from box-fitting (4.3, 4.4).
- PDF renderer: `pdftoppm` only (4.0).
- Subresource identifiers are signed via `django.core.signing`, not merely
  encoded (4.4).
- Cover rendering uses a fixed named-size set,
  `DOCUMENT_VIEWER_COVER_SIZES`, never arbitrary client-requested
  dimensions (4.1, 4.3).
- Config validation is lazy (checked on first filesystem use), not in
  `AppConfig.ready()` (4.2).
- `add_active` never adopts a pre-existing unregistered symlink and never
  invents a suffixed name for a collision; it reports the error. The manifest
  and link are updated under a lock, but cross-operation crash durability is
  not a v1 requirement. `documentview_reconcile_active`, run explicitly by an
  operator, reports mismatches and never modifies foreign entries (4.5).
- `remove_active` on a valid, registered, app-owned link always succeeds,
  regardless of the registered source's state: missing, replaced by a
  directory, unreadable, or no longer a supported suffix all still remove
  the symlink and manifest entry, each surfaced with its own specific
  user-facing reason rather than a generic error or a silent no-op (1.5, 4.5).
- `DOCUMENT_VIEWER_CACHE_DIR` defaults to `~/var/documentview/cache`,
  `DOCUMENT_VIEWER_ACTIVE_MANIFEST` to
  `~/var/documentview/active_manifest.json` (4.2) — still worth a quick
  confirmation against actual deployment paths before Step 1 lands, since
  `config.py` and the llime settings wiring both depend on them.

# UPGRADE: Image Blacklist for imhandler

This document replaces imhandler's deferred deletion plan with a persistent
image blacklist. Django never deletes, moves, or modifies source images in an
archive. Hiding an image records its path, and both the viewer and the
`imh`/imhandler pipeline exclude it. Authorized users can view blacklisted
files and restore them to normal processing. Although the feature does not
provide online file removal, its CLI export is intended to support
operator-controlled offline removal, including removal performed by separate
local scripts.

---

## Part 1 — Requirements

### 1.1 Replace deletion with hiding

Remove the current Mark workflow:

- Remove `mark_toggle`, `deletion_list_download`, and
  `deletion_list_clear`, their URL patterns, and the session-held
  `deletion_list`.
- Remove Mark/Unmark buttons, the fixed deletion bar, `delete.sh` generation,
  and their CSS and JavaScript.
- Add a **Hide** action where Mark is currently offered: cluster contact-sheet
  rows and the focal and closest-match images on the Similar page.

Hiding adds the image's canonical path to a persistent blacklist. It does not
unlink, rename, move, or otherwise modify the source file.

### 1.2 Blacklist behavior

Once blacklisted, an image:

- is absent from Browse, Similarity, Semantic, Compare, and Similar pages;
- receives HTTP 404 from the `image` and `thumb` endpoints, so an old URL
  cannot bypass the viewer filter;
- is excluded by `imh list`, `imh thumb`, `imh embed`, `imh cluster`,
  and `imh report`; and
- is treated by `imh purge` as ineligible derived data: its thumbnails,
  database row, and cluster memberships may be removed, but its source file
  must never be removed.

Existing thumbnails and database rows must not make a blocked image visible.
Blacklist matching is implemented once in the shared library, rather than
independently in Django and the CLI.

### 1.3 Hide and restore interactions

Clicking **Hide** opens a confirmation dialog with a thumbnail, the full path,
and text saying that the archive file remains on disk while imhandler stops
displaying and processing it. The dialog offers **Cancel** and **Hide**.
Submitting is POST-only and CSRF-protected; controls are disabled in flight,
errors remain in the dialog, and adding an existing entry is idempotent.

On success, update the page without a full reload. Remove the contact-sheet row
or replace the Similar panel with a **Hidden** notice. If fewer than two visible
cluster members remain, return to Compare.

Add an authorized **Hidden images** page listing blacklist entries, including
missing files, with a POST-only, CSRF-protected **Show again** action. Restoring
removes the path from the blacklist but does not recreate purged thumbnails,
embeddings, or clusters; normal viewer and `imh` workflows rebuild them.

The blacklist is exportable only through the local `imh` CLI. The web UI has
no download or export action. Export produces data that a separate local
script or manual process can use for explicitly offline file removal. The
export command itself does not delete files or emit an executable shell
script.

### 1.4 Path scope and identity

- Entries are absolute normalized paths beneath a configured `image_root`
  with a suffix in `scanner.IMAGE_SUFFIXES`.
- Reject paths outside all roots, traversal, and textual-prefix lookalikes.
- Identity is path-based. Replacement at the same path stays hidden; a rename
  has a new identity and is not automatically hidden.
- Use the same path representation as scanner output and database rows.
- Validation need not require the file to exist. This supports stale entries
  and keeps a later file at that path hidden until explicitly restored.
- Hiding does not require a writable image root. Archive roots may and normally
  should remain read-only to Django.

### 1.5 Persistence and concurrency

Store the blacklist at `cache_dir/blacklist.json`:

```json
{
  "version": 1,
  "paths": ["/home/yamada/Pictures/Archive/2024/example.jpg"]
}
```

Paths are unique and sorted. A missing file means an empty blacklist; malformed
or unsupported data is an explicit error, never silently empty. Updates use an
inter-process lock and atomic replacement so Django workers and CLI processes
cannot lose changes or observe partial JSON. Create it with owner-only
permissions and never write into a source-image directory.

### 1.6 Authorization and failure policy

- By default, hiding, listing, and restoring require an authenticated staff
  user. Hosts may provide an `IMHANDLER_BLACKLIST_AUTHORIZER` callable.
- Endpoint authorization is authoritative; hiding buttons is not sufficient.
- A blocked image endpoint returns 404 without revealing source-file existence.
- No blacklist operation invokes a source-path delete, move, rename, or shell
  command.
- Django does not expose the complete blacklist as a downloadable file or API
  response. The Hidden images page is for interactive review and restoration,
  not bulk export.
- If the blacklist cannot be read, image-serving and mutation endpoints fail
  closed. CLI commands report the error and exit nonzero instead of processing
  every image.
- Cleanup failure leaves the entry blocked and can be retried with `imh purge`.

### 1.7 Out of scope

- Deleting, moving, renaming, or modifying archive files.
- Trash or filesystem undelete.
- Bulk hide/restore.
- Content-hash or inode tracking across moves.
- Sharing a blacklist between variants that have different `cache_dir` values.
- Performing deletion or generating an executable deletion script from `imh`
  itself. Consuming an export with a separate, operator-invoked offline script
  is supported.

---

## Part 2 — Design

### 2.1 Shared blacklist module

Add `imhandler.blacklist` as the sole persistence and matching implementation:

```python
load() -> frozenset[Path]
is_blocked(path: Path | str, blocked: AbstractSet[Path] | None = None) -> bool
add(path: Path | str) -> bool
remove(path: Path | str) -> bool
```

`add` and `remove` return whether the set changed. They normalize and
validate the path, acquire the update lock, reload under that lock, and
atomically replace the file. The module uses `cache.cache_dir()`, configured
root lookup, and `scanner.IMAGE_SUFFIXES`, and contains no Django types.

### 2.2 Scanner and direct-entry integration

`scanner.scan()` and `scan_all()` accept an optional preloaded blocked set.
Each top-level operation loads the blacklist once and passes that immutable
snapshot through recursive scans. Filter before creating an `ImageEntry`.

This automatically covers Browse, list, thumbnail prewarming, and embedding.
Explicit-path library entry points that bypass scanning must call
`is_blocked` themselves. An album whose leaf images are all hidden follows
the existing empty-album rendering behavior.

### 2.3 Database, clustering, and purge

Old database rows require explicit filtering. Similarity, Semantic, Compare,
cluster detail, Similar, and reports load one blacklist snapshot and omit
matching rows. Clustering excludes blocked rows from its similarity matrix.
Reports omit blocked members and clusters with fewer than two visible members.

`imh purge` removes thumbnails, `ClusterMembership` rows, and `Images`
rows for blocked paths and collapses clusters with fewer than two members.
Every such write is confined to `cache_dir`; tests must assert that the
source archive is unchanged. Restored images regain derived state only through
later thumb/embed/cluster runs.

### 2.4 Django endpoints and templates

Replace deletion-list routes with:

| URL | Name | Method | Purpose |
|---|---|---|---|
| `hide/` | `hide_image` | POST | Add one image path |
| `hidden/` | `hidden_images` | GET | List entries |
| `restore/` | `restore_image` | POST | Remove one image path |

The normal `image` and `thumb` views check the blacklist after canonical
path validation and before opening or generating content. Page views filter
database results before building context.

Add a shared `_hide_modal.html` with plain JavaScript to show the existing
thumbnail and path, post the path and CSRF token, display errors, and invoke a
page-specific success callback. Add `hidden_images.html` with path,
source-exists status, and Show again form. Do not show thumbnails there because
media endpoints deliberately block them.

### 2.5 CLI behavior

| Command | Effect |
|---|---|
| `imh list` | Omit blocked paths from counts and output |
| `imh thumb` | Do not generate blocked thumbnails |
| `imh embed` | Do not create or refresh blocked embeddings |
| `imh cluster` | Exclude blocked database rows |
| `imh report` | Omit blocked members and undersized visible clusters |
| `imh purge` | Remove blocked derived state, never source files |
| `imh blacklist export` | Write the blacklist as offline, non-executable data |

Add:

```sh
imh blacklist export [-o FILE] [--format paths|json]
```

The default `paths` format writes one absolute path per line, sorted exactly as
in the store. `--format json` writes the versioned blacklist document. With no
`-o`, output goes to stdout so the operator can redirect it locally. `-o FILE`
uses atomic replacement and refuses to use the blacklist store itself as the
destination. Export is read-only: it does not clear entries, mutate derived
data, inspect or modify source files, or invoke another program.

The paths format is deliberately data, not shell syntax: paths are not quoted
as commands, and no shebang, `rm`, or other executable content is generated.
It is suitable as input to a separate, operator-invoked local script or manual
process that reviews and removes archive files offline. This assists offline
removal without giving the web application or the `imh` export command file
deletion capability.

Each configured variant uses its own `cache_dir` and blacklist. An intentionally
unconfigured `imh list DIR` has no blacklist to load and retains its current
behavior; document this exception.

### 2.6 Normative documentation changes

| File | Required update |
|---|---|
| `specs/imhandler-django-man.md` | Replace Mark/download with Hide and restore |
| `specs/imhandler-specs.md` | Add blacklist API and replace deletion-list state/views |
| `specs/imhandler-django-impl.md` | Update exports, URLs, authorization, templates, media blocking |
| `specs/imhandler-overview.md` | Describe non-destructive viewer hiding and CLI exclusion |
| `specs/imhandler-goals.md` | Replace deletion-list capability with blacklist capability |
| `specs/imhandler-imh-man.md` | Document every command, offline blacklist export, and the unconfigured-list exception |
| `specs/imhandler-imh-impl.md` | Document scan snapshots, DB filtering, clustering, purge, and export serialization |

Changes to format, validation, command coverage, authorization, restoration, or
cleanup semantics must update the affected specifications in the same change.

---

## Part 3 — Implementation Steps

### Step 1 — Persistent store

Add and test `lib/imhandler/blacklist.py`: versioned parsing, normalization,
root validation, missing and stale paths, idempotent updates, locking, atomic
replacement, multiple roots, malformed input, permissions, and concurrent
writers. Document the JSON as persistent user state, not disposable cache.

### Step 2 — Shared and CLI enforcement

Thread a blacklist snapshot through scans; audit explicit-path thumbnail and
embedding entry points; filter clustering and reports; and extend purge to
clean blocked derived state. Add command tests for list, thumb, embed, cluster,
report, and purge, including assertions that source files remain untouched.
Add export tests for stdout and files, stable ordering, both formats, malformed
stores, destination conflicts, and confirmation that export changes no state.

### Step 3 — Django replacement

Add `_can_manage_blacklist(request)` and the three endpoints; enforce blocking
in `image`, `thumb`, and page/query paths; replace Mark and deletion-bar UI
with Hide; add the Hidden images page; and remove old exports, routes, context,
session state, and shell-script generation.

### Step 4 — Specifications and verification

Apply section 2.6 and search for stale workflow language:

```sh
rg -n "deletion list|deletion_list|delete\.sh|mark_toggle|deletion-list|Mark button|marked for deletion|immediate deletion"
```

Then run:

```sh
python3 -m py_compile lib/imhandler/*.py lib/imhandler/cli/*.py lib/imhandler/djview/*.py bin/imh
python3 -m unittest discover -s tests -t .
cd llime && ./manage.py check
cd llime && ./manage.py test
```

Manually hide an image, confirm it appears on the Hidden images page, and
confirm that all viewer surfaces and old media URLs
block it while the source remains unchanged. Run every `imh` command, restore
the image, and confirm normal regeneration makes it eligible again. Also test a
missing path, concurrent updates, and a malformed blacklist. Export both
formats from the local CLI, confirm that neither output is executable, verify
that the paths output can be consumed safely as data by an offline script, and
confirm that no web route offers the same export.

### Step 5 — Rollout and rollback

Deploy the shared library and Django changes together, checking both `llime`
and `../qat/knip`. No archive write permission is required. Back up
`cache_dir/blacklist.json` as user-maintained state. Old code ignores the
file and would expose hidden images, so rolling back while the old viewer is
reachable is not policy-safe.

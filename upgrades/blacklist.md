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
imh blacklist export [-o FILE] [--format paths|paths0|json]
```

The default `paths` format writes one absolute path per line, sorted exactly as
in the store. `--format json` writes the versioned blacklist document.
`--format paths0` writes each absolute path terminated by a NUL byte instead
of a newline (the `find -print0`/`xargs -0` convention), sorted the same way.
With no `-o`, output goes to stdout so the operator can redirect it locally.
`-o FILE` uses atomic replacement and refuses to use the blacklist store
itself as the destination. Export is read-only: it does not clear entries,
mutate derived data, inspect or modify source files, or invoke another
program.

Unix filenames may contain a literal newline byte, which a line-oriented
format cannot represent: splitting output on newlines would silently turn one
blacklisted path into two apparent removal targets for an offline consumer,
one of which was never actually blacklisted. To keep the `paths` format safe
for its one supported consumption pattern (splitting on newlines), exporting
with `--format paths` must fail explicitly, before writing any output, if any
entry in the store contains a newline — the same "explicit error, never
silently wrong" policy already required of a malformed blacklist file
(section 1.5). `--format paths0` and `--format json` have no such
restriction: NUL cannot appear in a POSIX path, and JSON escapes embedded
newlines in its string encoding, so both represent every accepted path
exactly and are the correct choice for machine consumption of blacklists that
may contain such entries.

The paths formats are deliberately data, not shell syntax: paths are not
quoted as commands, and no shebang, `rm`, or other executable content is
generated. They are suitable as input to a separate, operator-invoked local
script or manual process that reviews and removes archive files offline
(`paths0` piping into `xargs -0` is the safe pattern for arbitrary filenames;
plain `paths` remains convenient for the common case and for manual review).
This assists offline removal without giving the web application or the `imh`
export command file deletion capability.

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

**Why this is the clean first step:** it is a standalone module under
`lib/imhandler/` with no Django, CLI, scanner, or database dependency — its
only inputs are `imhandler.appconfig`'s module globals (`cache_dir`,
consumed indirectly through `cache.cache_root()`) and `scanner.IMAGE_SUFFIXES`,
both already unit-tested in isolation (`tests/test_appconfig.py` shows the
exact mocking pattern: patch the `appconfig` module globals directly, no
Django settings or `manage.py` needed). It touches no existing code path —
nothing imports it yet — so it can be added, tested, and reviewed on its own
with zero regression risk to Browse, Similarity, or any `imh` command. Steps
2-5 (scanner/CLI enforcement, Django endpoints, spec updates, rollout) all
read this module's public API as a given, so it is also the one piece every
later step is blocked on.

This step also makes one small, additive change outside `blacklist.py`
itself: a new `cache.configured_image_roots()` function (see below), because
neither existing root helper is safe to call from path-validation code —
see the first design bullet.

**Design.**

- **Storage location:** `cache.cache_root() / 'blacklist.json'`, matching
  section 1.5 exactly (`cache_dir/blacklist.json`). The module creates
  `cache_dir` with `mkdir(parents=True, exist_ok=True)` if absent — same
  pattern `db.open_db()` already uses for `cache_dir/db/`.
- **Root-lookup helper (new, additive, in `cache.py`):** neither existing
  root function is safe to call from `_normalize()`. `scanner.py` does not
  even import `image_roots` (it only imports `image_root`/`image_root_entries`
  from `cache.py` — an earlier draft of this plan wrongly referenced
  `scanner.image_roots()`, which does not exist at all). And `cache.image_roots()`
  itself, via `image_root_entries()`, raises `EnvironmentError` for **every**
  configured root the instant *any one* of them fails `p.is_dir()` — correct
  for the existing Django views (`_get_roots()` catches that and renders a
  whole-page error, an accepted all-or-nothing failure mode for *serving*),
  but wrong for blacklist mutation: a user hiding/restoring an image under an
  available root must not fail merely because a second, unrelated archive
  volume happens to be unmounted right now, and section 1.4 explicitly wants
  stale-path support (a root can legitimately be offline). Add:
  ```python
  # cache.py
  def configured_image_roots() -> list[Path]:
      """Configured image_root paths, resolved but never required to exist.

      Unlike image_root_entries()/image_roots(), this does not stat() or
      require each root to currently be a directory -- callers that only
      need root *identity* for path validation (not to actually read files
      under it) use this so an offline/unmounted archive volume doesn't
      block validation for a path under a different, available root, or
      block validating a stale path under the offline root itself. Still
      raises EnvironmentError if image_root is entirely unconfigured, same
      as image_root_entries().
      """
      paths = appconfig.image_roots
      if not paths:
          raise EnvironmentError('image_root is not configured in etc/imhandler.conf')
      return [Path(p).expanduser().resolve() for p in paths]
  ```
  `Path.resolve()` defaults to `strict=False`: it does not raise for a path
  that doesn't exist (confirmed: `Path('/nonexistent/sub/../x').resolve()` →
  `/nonexistent/x`, no error) — it resolves symlinks for whatever prefix of
  the path currently exists and lexically collapses the rest. That is what
  makes this safe to call regardless of whether the root is mounted, and
  it's also *why* `_normalize()` below can use the same call for the
  candidate path without reintroducing an existence requirement.
- **Path identity:** a `_normalize(path: Path | str) -> Path` helper —
  - Expands `~`, then **checks absoluteness before resolving**:
    `expanded = Path(path).expanduser(); if not expanded.is_absolute(): raise
    ValueError(...)`. This ordering matters and is easy to get backwards —
    `Path.resolve()` always returns an absolute path (a relative input
    resolves against the current working directory), so checking
    `is_absolute()` *after* calling `resolve()` can never reject anything:
    if the process's CWD happens to be inside a configured root (plausible
    for a CLI invocation, e.g. `imh` run from within an archive directory),
    a relative input like `photo.jpg` would resolve to a real in-root,
    correct-suffix path and sail through every later check even though
    section 1.4 requires "absolute normalized paths" as the accepted input
    shape, not merely as the output shape. Only once the pre-resolve
    absoluteness check passes does `_normalize()` call `Path(...).resolve()`
    — the same non-strict resolution `cache.configured_image_roots()` now
    uses for roots, and, importantly, the same call the existing
    `image`/`thumb`/`similar` endpoints already use on the client-supplied
    `path` query parameter (`lib/imhandler/djview/__init__.py:492,621,656`:
    `path = Path(path_str).resolve()`). Reusing that exact convention (not
    the lexical-only `os.path.normpath` an earlier draft of this plan used)
    is what keeps stored identity and served identity from disagreeing: if
    a candidate path passes through an in-root symlink, `.resolve()`
    collapses it to the same real target the serving endpoints would
    independently resolve to, so a blacklist entry for the alias and a
    request for the target (or vice versa) are the same stored value — the
    alias/target split an earlier draft left open cannot arise, because
    both call sites now agree there is only one identity: the resolved
    path.
  - Checks containment with `any(normalized == root or
    normalized.is_relative_to(root) for root in cache.configured_image_roots())`
    — `Path.is_relative_to` compares path *segments*, so
    `/home/yamada/Pictures/ArchiveEvil/x.jpg` is correctly rejected against
    a configured root of `/home/yamada/Pictures/Archive` (the textual-prefix
    lookalike section 1.4 calls out; a `str.startswith()` check would wrongly
    accept it). Both sides of the comparison went through the same
    `.resolve()`, so this is also symlink-consistent, not just
    string-consistent. Raises `ValueError` if no configured root contains
    the path.
  - Checks `normalized.suffix.lower() in scanner.IMAGE_SUFFIXES`, raising
    `ValueError` otherwise.
  - `add()` and `remove()` both call this, so they share one rejection
    path for traversal, out-of-root, and wrong-suffix inputs. `is_blocked()`
    deliberately does **not** call it: it's the hot path (once per file
    during a scan of potentially thousands of images), and every caller
    already guarantees a normalized, in-root, correct-suffix path before
    checking it — the scanner only ever calls it with a `Path` it just built
    under a resolved root, and section 2.4's endpoints check the blacklist
    "after canonical path validation" has already run (and, per the point
    above, will have already run the identical `.resolve()`). `is_blocked`
    is a plain `Path(path) in blocked` lookup.
- **On-disk format:** exactly the section-1.5 shape, `paths` always written
  sorted for a stable diff/`git`-friendly file even though it's not meant to
  be hand-edited. `load()` raises a new `BlacklistError` (module-level
  exception, not a bare `ValueError`, so callers can distinguish "bad input
  path" from "store is corrupt") when the file exists but `json.load` fails,
  `version` is missing or not `1`, `paths` is not a list of strings, or **any
  individual entry fails structural validation** — checked with a
  `_validate_stored_entry(raw: str) -> Path` helper applied to every entry:
  non-empty, absolute, no embedded NUL byte (`'\x00' not in raw` — NUL can
  never appear in a real POSIX path and always indicates corruption), a
  suffix in `scanner.IMAGE_SUFFIXES`, and — the check that closes the actual
  gap — `os.path.normpath(raw) == raw`, i.e. the string is already in its
  own canonical lexical form. This is what catches a malformed entry like
  `/archive/sub/../photo.jpg` sitting next to a scanner-produced
  `/archive/photo.jpg`: today's plain "list of strings" schema check would
  accept it, and since `is_blocked()` is a bare set-membership check (by
  design, for speed), the two strings would never compare equal and the
  intended file would silently stay visible. This check is **deliberately
  lexical only** — it does not call `.resolve()` or re-check root
  containment against the *current* `appconfig.image_roots` at load time:
  re-resolving would touch the filesystem and could legitimately disagree
  with what was stored if something on disk changed since, and re-checking
  containment would reject a historically-valid entry purely because
  `image_root` was reconfigured afterward, which section 1.4 does not ask
  for (a stale/offline root must not evict its existing entries). It only
  rejects strings that could never have come out of `_normalize()` in the
  first place, regardless of today's config. Per section 1.5, "malformed or
  unsupported data is an explicit error, never silently empty." A
  **missing** file is the one case that legitimately means an empty
  blacklist: `load()` catches `FileNotFoundError` specifically and returns
  `frozenset()`, nothing broader.
- **Locking:** a dedicated `cache_dir/.blacklist.lock` file (never the data
  file itself), taken with `fcntl.flock(fd, fcntl.LOCK_EX)` for the
  reload-under-lock / compute / atomic-replace critical section in `add()`
  and `remove()`. This is the first inter-process lock in the codebase
  (`storage.py`'s `_replace_or_move` handles atomic replacement but not
  locking, and `db.py` relies on SQLite's own locking) — `fcntl` is
  Linux/POSIX-only, consistent with the rest of `imhandler`'s existing
  filesystem assumptions and this deployment's environment, so no
  cross-platform fallback is planned.
- **Atomic replacement:** `tempfile.mkstemp(dir=cache_dir, prefix='.blacklist-')`
  (created `0600` by `mkstemp` itself, satisfying section 1.5's "owner-only
  permissions" with no extra `chmod`), write + `flush()` + `os.fsync()`,
  then `os.replace(tmp, store_path)` — same-filesystem rename, so no
  `EXDEV` fallback is needed the way `storage.py._replace_or_move` needs one
  for cross-media-root moves. On any exception during the write, the temp
  file is unlinked before re-raising so a failed write never leaves stray
  `.blacklist-*.tmp` files behind.
- **Public API** (signatures as already fixed in section 2.1):
  ```python
  class BlacklistError(Exception):
      """The blacklist store exists but is corrupt or unsupported."""

  def load() -> frozenset[Path]: ...
  def is_blocked(path: Path | str, blocked: AbstractSet[Path] | None = None) -> bool: ...
  def add(path: Path | str) -> bool: ...     # raises ValueError on an invalid path
  def remove(path: Path | str) -> bool: ...  # raises ValueError on an invalid path
  ```
  `add`/`remove` both: normalize+validate first (outside the lock — no
  reason to hold it just to raise `ValueError`), then acquire the lock,
  reload the current on-disk set fresh (never trust an in-memory copy across
  the lock acquisition — that's precisely the lost-update race the lock
  exists to prevent), compute the new set, write it only if it actually
  changed, and return whether it changed. An `add()` of an
  already-present path or a `remove()` of an absent one is a no-op that
  still returns `False` — this is what makes hiding idempotent (section 1.3)
  and gives the Django view a simple truthy check for "did anything happen."
  **Known limitation, deliberately not solved in Step 1:** `remove()` uses
  the same `_normalize()` as `add()` — including the current-config root
  containment check — so a stored path that would no longer validate against
  the *current* configured roots (e.g. `image_root` was reconfigured after
  the entry was added, or the entry's root is a currently-offline volume
  outside every *currently* configured root) cannot be removed through this
  API, even though `load()` (see above) still surfaces it correctly and
  `is_blocked()` still blocks it. This is a real, tested gap, not a
  hypothetical one: covered by a test below rather than left implicit. Out
  of scope to solve until Step 3's restore endpoint makes "remove an entry
  `_normalize()` would now reject" a real product question (e.g. whether
  restore should bypass containment entirely, since restoring only ever
  narrows the blocked set).

**Tests** — new `tests/test_imhandler_blacklist.py`, following
`tests/test_appconfig.py`'s pattern (insert `lib/` onto `sys.path`, import
`imhandler.blacklist` and `imhandler.appconfig` directly, no Django). Each
test sets `appconfig.cache_dir` and `appconfig.image_roots` to a
`tempfile.TemporaryDirectory()` via `mock.patch.object`, so nothing touches
a real archive or a real cache dir:

- `load()` on a missing file returns `frozenset()`.
- `add()` then `load()` round-trips one path; `add()` of the same path again
  returns `False` and leaves the file's mtime/content unchanged (idempotent,
  no spurious write).
- `add()` rejects: a relative path, a path outside every configured root, a
  path under a root only as a textual prefix (e.g. sibling directory whose
  name extends the root's, proving `is_relative_to` and not
  `startswith` is used), a `..`-traversal path that lexically escapes the
  root, and a wrong-suffix file — each as a distinct `ValueError` case.
- **Relative input with an in-root working directory** (the absoluteness
  ordering bug): `chdir()` into a configured root's temp directory, then
  call `add('photo.jpg')` (no leading `/`) and assert it still raises
  `ValueError`. Checking `is_absolute()` *after* `resolve()` would pass this
  case silently — `Path('photo.jpg').resolve()` becomes an in-root,
  correct-suffix absolute path from the CWD alone — so this test only passes
  once the absoluteness check runs on the pre-resolve, `expanduser()`-only
  path.
- `add()` accepts a path that does not exist on disk (stale-entry support,
  section 1.4) and `load()` still returns it.
- `remove()` of a present path returns `True` and drops it from `load()`;
  `remove()` of an absent path returns `False` and writes nothing.
- Multiple configured roots: `add()` accepts a path under the second root
  when only the first root would reject it.
- **Unavailable configured root** (`cache.configured_image_roots()`, Finding
  1): a direct test of the new function itself — two configured roots, one
  pointed at a path that does not exist on disk at all — asserts it returns
  both resolved paths without raising (unlike `image_roots()`, exercised
  separately in `tests/test_appconfig.py`'s style but for `cache.py`, kept
  in this same new test file since the function is small and only exists
  for `blacklist.py`'s sake for now). Plus, at the `blacklist.py` level with
  the same two-roots-one-missing fixture: (a) `add()`/`remove()` for a path
  under the *other*, present root still succeeds, proving root validation no
  longer calls the existence-requiring `image_root_entries()`/`image_roots()`;
  (b) `add()`/`remove()` for a path nominally under the *missing* root itself
  also succeeds (containment doesn't require the root to currently exist),
  directly exercising the "compatible with stale paths" requirement, not
  just the "other root is fine" half.
- **Symlink identity consistency** (Finding 2): inside a temp root, create a
  real file and a symlink to it elsewhere under the same root; `add()` the
  symlink's path; assert the entry `load()` returns is the *real* file's
  resolved path, not the symlink's own path — i.e. asserting the stored
  value equals `Path(symlink_path).resolve()`, the same computation
  `lib/imhandler/djview/__init__.py`'s `image`/`thumb` endpoints perform on
  a request path. This is the test that would have caught the original
  divergence: with the old lexical-only normalization, this assertion would
  have failed because the stored entry would have been the symlink's own
  (unresolved) path instead.
- **Load-time structural validation** (Finding 3): hand-write a store whose
  `paths` list contains one syntactically-valid-JSON-string entry that
  `_normalize()` itself could never have produced —
  `/archive/sub/../photo.jpg` (a non-canonical `..` segment) is the
  motivating case, plus a relative path, a path with an embedded `\x00`,
  and a wrong-suffix path, each as a separate test — and assert `load()`
  raises `BlacklistError` for each, never silently returning a set that
  contains the bad string as-is. A companion test proves this check stops
  at syntax, not config: write a store containing one *well-formed*,
  `_normalize()`-producible entry (absolute, canonical, correct suffix)
  whose root is **not** among the currently-configured `image_roots` (i.e.
  reconfigured away since it was added) and assert `load()` still returns
  it without raising — this is the "preserving valid historical entries
  after root reconfiguration" half of the requirement, and it's what
  distinguishes this from simply re-running `_normalize()` at load time
  (which would have rejected it and thereby actively evicted a real
  historical entry, the opposite of what section 1.4 wants). Chain that
  same fixture into the already-planned "known limitation" test: `remove()`
  of that same now-unreachable-by-root path raises `ValueError`, while
  `is_blocked()` against the `load()`-returned snapshot still correctly
  reports it as blocked — pinning down, in one test, that the entry is
  fully visible/enforced but only removal is affected.
- A hand-written malformed store — invalid JSON, `"version": 2`, and
  `"paths"` containing a non-string — each raises `BlacklistError` from
  `load()` (and therefore from `add()`/`remove()`, which call it under the
  lock), never returns an empty/partial result.
- The written file has mode `0600` (via `stat().st_mode`) and its parent
  directory is created if missing.
- A concurrency test: use a `ThreadPoolExecutor` to call `add()` for N
  distinct valid paths at once against the same store; assert all N survive
  in the final `load()` (proves the lock's reload-under-lock actually
  prevents a lost update rather than just serializing on paper).
- `is_blocked()` with an explicit `blocked` set skips disk entirely (assert
  via a `mock.patch.object(blacklist, 'load')` that raises if called) —
  confirms callers can pass a cached snapshot for the hot path described
  above.

**Verification:**
```sh
python3 -m py_compile lib/imhandler/blacklist.py lib/imhandler/cache.py
python3 -m unittest tests.test_imhandler_blacklist -v
python3 -m unittest discover -s tests -t .
```
No `manage.py check`/`manage.py test` needed for this step — nothing Django
imports it yet.

### Step 2 — Shared and CLI enforcement

Thread a blacklist snapshot through scans; audit explicit-path thumbnail and
embedding entry points; filter clustering and reports; and extend purge to
clean blocked derived state. Add command tests for list, thumb, embed, cluster,
report, and purge, including assertions that source files remain untouched.
Add export tests for stdout and files, stable ordering, all three formats,
malformed stores, destination conflicts, confirmation that export changes no
state, and adversarial filenames: paths containing embedded newlines (must
error before writing any output in `paths` format; must round-trip exactly
in `paths0` and `json`), and other shell-metacharacter-heavy names (spaces,
quotes, backslashes) to confirm no format ever produces executable or
misquoted output.

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

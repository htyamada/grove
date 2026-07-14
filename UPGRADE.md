# UPGRADE: Immediate Deletion for imhandler

This document proposes replacing imhandler's deferred deletion model (the
session-based "deletion list" plus downloadable `delete.sh`) with immediate,
confirmed, in-browser deletion.

The document has three parts: **Requirements**, **Design**, and
**Implementation Steps**.

---

## Part 1 — Requirements

### 1.1 Remove the deferred deletion model

The current workflow — Mark buttons on the cluster contact sheet and Similar
pages, a session-held `deletion_list`, the fixed bottom deletion bar, the
`delete.sh` download, and the "clear list" action — is removed entirely:

- Remove the `mark_toggle`, `deletion_list_download`, and
  `deletion_list_clear` views and their URL patterns (`mark/`,
  `deletion-list/`, `deletion-list/clear/`).
- Remove the session key `deletion_list` and all reads/writes of it.
- Remove the Mark/Unmark buttons, the `#del-bar` deletion bar, and all
  associated CSS/JS from `cluster_detail.html` and `similar.html`.
- The web UI no longer generates or serves shell scripts.

### 1.2 Add immediate deletion

- A **Delete** button appears wherever a Mark button appears today:
  - each row of the cluster contact sheet (`cluster_detail.html`);
  - the focal image and the closest match on the Similar page
    (`similar.html`).
- Delete is available only when the current request is authorized to delete
  and the image belongs to a writable image root. Read-only images show a
  **Read only** label instead of a Delete button.
- Clicking Delete never removes anything directly; it opens a confirmation
  dialog first.
- On confirmation, the file is deleted from disk immediately, and the
  associated bookkeeping is cleaned up (see 1.4).

### 1.3 Confirmation dialog

The confirmation dialog must present, for the image about to be deleted:

1. a **thumbnail** of the image;
2. the **full filesystem path**;
3. the file **date** (modification time);
4. the **pixel dimensions** (width × height).

Behavioral requirements:

- When the file is deletable, the dialog offers exactly two actions:
  **Cancel** (default, safe) and **Delete** (destructive, visually distinct —
  red). Missing and read-only states offer only **Close**.
- `Escape` and clicking outside the dialog cancel it unless a deletion request
  is already in flight.
- The path, modification time, dimensions, and thumbnail are all derived from
  one stable open-file snapshot at confirmation time. The thumbnail is returned
  with that metadata rather than fetched separately through the browser cache.
  If a stable snapshot cannot be obtained or the file no longer exists, the
  dialog offers no Delete action.
- A deletable snapshot includes a short-lived, one-use confirmation ticket.
  Delete accepts that ticket, not a client-supplied path, and revalidates the
  current file against the snapshot before unlinking. If the file was modified,
  replaced, renamed, or its root became read-only, deletion is rejected and the
  user must review newly fetched information and confirm again.
- While the deletion request is in flight the Delete button is disabled to
  prevent double-submission.
- On failure, the error message is shown inside the dialog; nothing on the
  page changes.
- On success the dialog closes and the page updates without a full reload: the
  row is removed from the contact sheet, or the panel is replaced by a
  "deleted" notice on the Similar page. A cluster that has fallen below two
  members instead navigates back to Compare.
- Once the deletion request has started, Cancel, `Escape`, and outside-click
  dismissal are disabled until the request finishes. A destructive request
  cannot continue behind a dismissed dialog. On failure, dismissal is restored
  after the error is displayed.

### 1.4 Consistency after deletion

Deleting an image must also:

- delete the image's rows from the dedup database (`Images` and
  `ClusterMembership`);
- remove affected `Clusters` that now contain fewer than two members, including
  their remaining membership rows;
- remove the image's cached thumbnails (all sizes) from
  `cache_dir/thumbs/`.

Neither cleanup failure may leave the UI claiming the file still exists: the
file unlink is the authoritative operation; DB/thumbnail cleanup errors are
logged but do not fail the request once the file is gone.

### 1.5 Read-only image roots

Each table entry in `image_root` accepts an optional `read_only` Boolean:

```toml
[hty7.imhandler.core]
image_root = [
    {path = "~/Pictures/Archive", name = "Archive", read_only = true},
    {path = "~/Pictures/Working", name = "Working", read_only = false},
]
```

- `read_only` defaults to `true`, including for legacy string entries and table
  entries that omit it. A root permits deletion only when it explicitly sets
  `read_only = false`; upgrading therefore enables no deletion by default.
- A read-only root remains available to Browse, Similarity, Semantic, Compare,
  thumbnail generation, embedding, and cache/DB maintenance. The flag prevents
  source-image deletion by the web UI; cache files remain writable.
- If configured roots overlap, the most-specific matching root controls the
  path. Duplicate resolved roots with conflicting access flags are a
  configuration error.
- Read-only state is enforced again by `delete_image`; hiding a button is not a
  security boundary. A direct deletion request for a read-only path returns
  HTTP 403 and does not alter the file, DB, or thumbnail cache.

### 1.6 Safety and security

- Deletion is `POST`-only and CSRF-protected (unlike `embed_cancel`, it must
  **not** be `csrf_exempt`).
- Deletion and its confirmation metadata require explicit authorization. By
  default, the request user must be authenticated and staff. A host may supply
  an `IMHANDLER_DELETE_AUTHORIZER` callable for a different deployment policy;
  the default remains deny-by-default for anonymous users. Authorization is
  enforced by both new endpoints, not only by button visibility. CSRF is not a
  substitute for authorization.
- The target must be an absolute, canonical path with no symlink components,
  resolve under a configured `image_root`, have a suffix in
  `scanner.IMAGE_SUFFIXES`, and be a regular file. The existing `image` and
  `thumb` validation is not sufficient for a destructive endpoint because it
  accepts arbitrary regular files under a root.
- The target's matching root must be writable according to 1.5.
- Only single-file deletion is supported. No bulk deletion, no directory
  deletion.
- No recycle bin / undo is provided; the confirmation dialog is the safety
  mechanism. (This matches `mediaview.delete_file`, the sibling app's
  precedent.)
- A root should be marked writable only when imhandler has exclusive control of
  deletion decisions for its directory entries. Roots with uncontrolled
  concurrent writers remain read-only; see the snapshot guarantee in 2.2–2.3.
- Deletion is enabled only on a platform that provides no-follow file opens,
  descriptor and directory-entry stat identity, and advisory file locking. If
  those primitives are unavailable, confirmation may show verified metadata
  but must not issue a deletion ticket.

### 1.7 Documentation

Specs must be updated to describe the new workflow and to remove the deferred
model: `specs/imhandler-django-man.md` (Deletion workflow section),
`specs/imhandler-specs.md` (view table, session-state section),
`specs/imhandler-django-impl.md` (view exports, URL table, session table),
`specs/imhandler-overview.md` (Delete workflow and configuration),
`specs/imhandler-goals.md` (remove the deletion-list reference), and
`specs/imhandler-imh-man.md` (document the root-table form). The commented
examples in `etc/imhandler.conf` must include `read_only`.

Section 2.9 is the normative specification change record. Any implementation
change that alters request/response shapes, defaults, safety guarantees, UI
behavior, or cleanup semantics must update both that record and the affected
spec documents in the same change; implementation and specs may not diverge
silently.

### 1.8 Testing

Automated tests must cover authorization, CSRF, method restrictions, strict
image-path validation, writable and read-only roots, successful cleanup,
best-effort cleanup failures, and the new template states. Existing test URL
patterns must be updated before the old view methods are removed.

### 1.9 Out of scope

- Delete buttons on the Browse/Similarity gallery grids and Semantic results
  (can be added later; the design below deliberately makes the dialog
  reusable).
- Trash/undelete support.
- Any change to the `imh` CLI (`purge` remains as-is and still covers
  externally deleted files).

---

## Part 2 — Design

### 2.1 Overview

Two new endpoints on `ImageHandlerViewSet` replace the three removed ones:

| URL | Name | Method | Purpose |
|---|---|---|---|
| `file-info/` | `file_info` | GET | Return live metadata for the confirmation dialog |
| `delete/` | `delete_image` | POST | Delete one image and clean up DB + thumbnails |

Both templates share a single confirmation-dialog include,
`image_handler/_delete_modal.html`, containing the modal markup, its CSS, and
its JS (imhandler.djview ships no static files today; inline in a shared
include keeps that property).

Root access and request authorization are evaluated server-side before either
endpoint responds. Templates use the same helpers to avoid presenting actions
that the endpoint will reject, but endpoint enforcement is authoritative.

### 2.2 `file_info` view (GET)

Query parameter: `path` (absolute path string, as already used by `thumb`,
`image`, and `similar`).

1. Require deletion authorization. Unauthorized requests return
   `JsonResponse({'error': 'Deletion is not authorized'}, status=403)`.
2. Validate the supplied path with the strict deletion-path helper: absolute,
   canonical/no symlinks, supported image suffix, and under the most-specific
   configured root. Invalid paths return status 400. A valid image path that is
   no longer a file returns `JsonResponse({'exists': false, ...})` with status
   200 (the dialog renders a "file is gone" state; it is not an application
   error).
3. Open the source once with no symlink following (`O_RDONLY | O_CLOEXEC |
   O_NOFOLLOW`) and take an `fstat()` before reading. From that
   same open file description:
   - read the encoded pixel dimensions with Pillow;
   - generate the confirmation thumbnail in memory at the dialog size.
4. Take a second `fstat()` after all reads. The before/after values for device,
   inode, size, `mtime_ns`, and `ctime_ns` must match. If they do not, discard
   every result and retry the complete snapshot once. A second mismatch returns
   HTTP 409 with no confirmation ticket. A dimension or thumbnail decoding
   failure returns HTTP 422 with no confirmation ticket; required confirmation
   information is never guessed, omitted, or taken from the DB.
5. For a writable root, create a signed, short-lived confirmation ticket
   containing the canonical path, matched root, complete stat fingerprint,
   issue time, and a random nonce. Register the nonce as unused in
   short-lived server-side confirmation state. The ticket expires after five
   minutes and is valid for one deletion attempt.
6. Respond with `Cache-Control: no-store`:

```json
{
  "exists": true,
  "path": "/home/yamada/Photos/img001.jpg",
  "name": "img001.jpg",
  "mtime": "2026-05-14 18:03:22.123456000 -10:00",
  "mtime_ns": 1778817802123456000,
  "width": 4032,
  "height": 3024,
  "thumbnail": "data:image/jpeg;base64,...",
  "read_only": false,
  "deletable": true,
  "confirmation_token": "signed-short-lived-token"
}
```

`mtime` is formatted server-side in the configured local timezone with seconds,
all nine fractional-second digits from `mtime_ns`, and numeric UTC offset;
`mtime_ns` also preserves the filesystem value used by validation. Width and
height are the encoded pixel matrix dimensions, before any EXIF
display-orientation transform. `thumbnail` is generated from the same open
source snapshot and embedded in the response, so neither the browser cache nor
the existing path-based thumbnail cache can substitute stale pixels.

For a read-only root the metadata response remains status 200 and contains
`"read_only": true`, `"deletable": false`, and a short `reason`, but no
confirmation ticket; the dialog shows the verified snapshot and a read-only
state with no Delete action. This also handles a stale page whose root
configuration changed after it rendered.

The ticket binds the displayed path, date, dimensions, and thumbnail source to
one verified file state. Page context and dedup DB metadata are never accepted
as confirmation data.

### 2.3 `delete_image` view (POST)

POST body: `confirmation_token` only (form-encoded). A raw client-supplied path
is not accepted. CSRF is enforced. The view is decorated with `require_POST`
and the authorization check runs before token or path details are returned.

1. Reject an unauthorized request with status 403.
2. Verify the signature, five-minute age, nonce, and unused state, then consume
   the ticket before any unlink attempt. An invalid ticket returns status 400;
   an expired, previously used, or stale ticket returns status 409 and requires
   a new confirmation snapshot.
3. Re-run strict path and most-specific-root resolution on the canonical path
   in the ticket. The root must still exist and explicitly set
   `read_only = false`; otherwise return status 403 without touching any file,
   DB row, or thumbnail.
4. Reopen the path without following symlinks, obtain a non-blocking exclusive
   advisory lock, and compare its device, inode, size, `mtime_ns`, and
   `ctime_ns` to the ticket. Immediately before unlink, `lstat()` of the
   directory entry must identify the same device/inode as the open descriptor. Any mismatch, inability to get
   a stable snapshot, or inability to get the lock returns status 409 with
   `{'error': 'File changed; review the current information', 'refresh': true}`.
   Nothing is deleted.
5. With the verified descriptor and lock still held, `path.unlink()`.
   `OSError` → `JsonResponse({'error': str(e)}, status=500)`.
6. Best-effort cleanup (errors logged to stdout in the `purge` style, never
   fatal, always still returns success once the unlink happened):
   - **DB** — open the dedup DB; select `id` from `Images WHERE path = ?`
     (all mtime variants); delete matching `ClusterMembership` rows, then the
     `Images` rows. Track affected cluster ids and delete any affected cluster
     now containing fewer than two members, including its remaining membership
     rows. Commit the DB work as one transaction.
   - **Thumbnails** — new `thumbnailer.invalidate(path)`:
     `digest = sha256(str(path))`; unlink every
     `thumbs_dir()/digest[:2]/{digest}-*.jpg` (one file per generated size).
     This mirrors `mediaview.thumbs.invalidate`.
   DB and thumbnail cleanup run in separate `try` blocks so failure of one does
   not prevent the other.
7. Respond `JsonResponse({'ok': true})` with `Cache-Control: no-store`.

If the dedup DB cannot be opened (no `cache_dir`), step 6's DB portion is
skipped silently — Browse-only deployments must still be able to delete.

POSIX does not provide a portable atomic “compare this fingerprint and unlink
this directory entry” operation. The supported correctness guarantee therefore
requires writable roots not to have uncooperative processes replacing directory
entries during deletion; such roots must keep the default `read_only = true`.
Within that declared operating boundary, the stable snapshot, exclusive lock,
immediate inode check, and one-use ticket ensure that deletion applies to the
file whose information the user reviewed. The fingerprint comparison is a stat
fingerprint, not a content hash: it guards against accidents — background
renames, replacements, and rewrites between confirmation and deletion — not
against an adversarial writer that forges timestamps. A deployment that cannot
satisfy this boundary must not enable deletion for that root.

### 2.4 Confirmation dialog (`_delete_modal.html`)

A single hidden overlay per page, populated on demand:

```
┌──────────────────────────────────────┐
│  Delete this image?                  │
│                                      │
│  ┌────────────┐  img001.jpg         │
│  │   thumb    │  /home/yamada/Photos│
│  │  (≤240px)  │  /img001.jpg        │
│  └────────────┘  exact local mtime   │
│                  4032 × 3024        │
│                                      │
│              [ Cancel ]  [ Delete ]  │
└──────────────────────────────────────┘
```

- Markup: `#delete-modal` overlay (`position: fixed; inset: 0`) above the
  existing lightbox z-index, containing thumbnail `<img>`, name, full path
  (wrapped, `word-break: break-all`), date line, dimensions line, an error
  line, and the two buttons. The include also renders `{% csrf_token %}` so
  the JS keeps working after the deletion-bar forms (today's only CSRF
  source) are removed.
- The modal has dialog semantics (`role="dialog"`, `aria-modal="true"`), moves
  initial focus to Cancel, traps focus while open, and restores focus to the
  invoking Delete button when it closes. All buttons use `type="button"`.
- JS API: `imhDeleteConfirm(path, onDeleted)`.
  1. `fetch(FILE_INFO_URL + '?path=' + encodeURIComponent(path))`; populate
     and show the modal. Non-2xx, network, and JSON errors are rendered inside
     the modal. If `exists` is false, show "File no longer exists" with only a
     Close button. If `deletable` is false, show the supplied reason with no
     Delete action. Set the thumbnail directly from the returned snapshot data
     and retain its `confirmation_token` only in the open modal's JS state.
  2. Confirm → `fetch(DELETE_URL, {method: 'POST', ...})` with the CSRF
     header and `confirmation_token` body. Never reconstruct or submit the path.
     Disable Delete and Cancel and ignore `Escape` / outside clicks while
     pending; the server request cannot safely be cancelled once sent.
  3. `ok` → close modal, call `onDeleted(path)`. Error → render the message
     in the modal's error line and re-enable both actions and dismissal. A 409
     with `refresh: true` invalidates the old modal data, fetches a new snapshot,
     and requires a new explicit Delete click after the user reviews it; it
     never retries deletion automatically.
  4. `Escape` and overlay-background clicks close the modal (guarded so they
     don't collide with the lightbox's `Escape` handler: the modal handler
     runs only while the modal is open and uses `stopImmediatePropagation()`).

### 2.5 Page integration

**`cluster_detail.html`** — for an authorized request and writable root, the
Mark button cell becomes a Delete button (`class="delete-btn"`, `data-path` as
today); read-only members show a label instead. `onDeleted` removes the `<tr>`
and corresponding `MEMBERS` entry. It then recomputes every remaining
thumbnail's `data-idx` (or resolves indexes by path at click time), because the
current lightbox indexes are fixed at render time. If one or zero data rows
remain, navigate to `back_url`; the cluster no longer represents duplicates and
the DB helper has removed it. Delete the `is-marked` styling, `#del-bar`, `body`
bottom padding, and mark JS.

**`similar.html`** — the focal and closest panels' Mark buttons become Delete
buttons when authorized and writable, otherwise they show a read-only or
deletion-unavailable state. `onDeleted`: for the focal image, replace the page content
area with a short "Image deleted" notice plus the existing back-link
(the rest of the page is meaningless without its focus); for the closest
match, replace that panel's contents with a "Deleted" note. Remove
`#del-bar`, marked styling, and mark JS. The `marked` /
`deletion_count` context variables disappear from the `similar` and
`cluster_detail` views. Their replacement context records each image's root
access and the request's deletion authorization.

### 2.6 URL and view-surface changes

`imhandler/djview/urls.py`:

```python
- path('mark/', views.mark_toggle, name='mark_toggle'),
- path('deletion-list/', views.deletion_list_download, name='deletion_list_download'),
- path('deletion-list/clear/', views.deletion_list_clear, name='deletion_list_clear'),
+ path('file-info/', views.file_info, name='file_info'),
+ path('delete/', views.delete_image, name='delete_image'),
```

`imhandler/djview/views.py` re-exports change accordingly. Consumers (`llime`
and `../qat/knip`) include `imhandler.djview.urls` wholesale, so they pick up
the URL change with no host URL edits; nothing in either host references the
removed URL names directly (to be re-verified during implementation). A host
settings edit is needed only when overriding the default deletion authorizer.

### 2.7 Shared code placement

- Add an immutable root-entry record in `imhandler.cache` containing resolved
  `path`, display `name`, and `read_only`. `image_root_entries()` returns these
  records; update its scanner and Django callers. `image_roots()` and
  `image_root()` retain their existing public return types.
- Add a most-specific-root lookup helper. It rejects duplicate resolved roots
  with conflicting flags and is the single source of truth for read-only
  enforcement.
- Path validation is currently duplicated across `thumb` / `image` /
  `similar`. Extract a module-level strict deletion helper that accepts a path
  string and returns the canonical `Path` plus its root entry. It rejects
  relative paths, non-canonical/symlinked paths, unsupported image suffixes,
  and paths outside all roots. Use it from both new views. Existing read-only
  `image` and `thumb` behavior remains unchanged; they may share only the
  non-destructive subset of the helper.
- `thumbnailer.invalidate(path: Path) -> int` (returns count removed) sits
  next to `purge` and reuses `_thumb_path`'s digest convention.
- DB row removal: new `db.delete_image_rows(conn, path: str) -> int` next to
  `cleanup_missing_members`, so the SQL stays in `db.py` with the other
  queries. It also removes affected clusters with fewer than two members in
  the same transaction.

### 2.8 Configuration and authorization

`imhandler.appconfig` adds `image_root_read_only: list[bool]`, parallel to the
existing root and name lists. `init()` accepts only TOML Booleans for the
optional table field and supplies `true` for string roots and omitted fields.
The `etc/imhandler.conf` examples document both values without opting either
currently configured deployed root into deletion.

The shared Django view layer adds `_can_delete(request)`. Its default policy is
`request.user.is_authenticated and request.user.is_staff`. A host can set
`IMHANDLER_DELETE_AUTHORIZER` to a callable or dotted callable path accepting a
request and returning a Boolean, for example when authenticated identity is
provided by a trusted reverse proxy. A custom authorizer must be documented in
the host settings; forwarded identity headers must never be trusted unless the
proxy strips client-supplied copies. An exception from the authorizer is logged
and denies deletion.

`file_info`, `delete_image`, `cluster_detail`, and `similar` all use
`_can_delete`; only the endpoints are security boundaries. The templates use
the result solely to present the correct controls.

Short-lived ticket state uses `request.session['delete_confirmations']`, a
bounded set of nonce/expiry records only; paths and metadata remain in the
signed ticket. `file_info` removes expired entries and keeps at most eight live
nonces per session. `delete_image` marks and saves the nonce as consumed before
opening or unlinking the file. This state is an anti-replay mechanism, not a
replacement for the removed cross-page `deletion_list` workflow.

### 2.9 Specification change record

This proposal changes the documented contract, not only implementation detail.
The implementation must update every affected spec in the same change and keep
the following record accurate if the design changes:

| Document | Contract changes to record |
|---|---|
| `specs/imhandler-django-man.md` | Deferred Mark/download workflow removed; immediate confirmation workflow added; deletion is authorization-gated and disabled unless a root explicitly sets `read_only = false`; stale-ticket refresh behavior documented. |
| `specs/imhandler-specs.md` | `image_root` table gains `read_only` with a default of `true`; root-entry/lookup APIs, snapshot response, confirmation-token request, status codes, anti-replay session state, thumbnail invalidation, and DB cluster collapse are documented. |
| `specs/imhandler-django-impl.md` | View exports/URLs, authorizer policy, strict path checks, stable-snapshot algorithm, ticket creation/revalidation, no-store responses, modal states, context fields, and cluster/lightbox updates are documented. |
| `specs/imhandler-overview.md` | Web deletion and root configuration summaries state the safe read-only default and explicit writable opt-in. |
| `specs/imhandler-goals.md` | The deletion-list capability is replaced with authorized, confirmed, snapshot-bound single-image deletion. |
| `specs/imhandler-imh-man.md` | Root-table syntax includes `read_only`; the flag defaults to `true` and affects web source deletion only, not cache-maintenance CLI commands. |
| `etc/imhandler.conf` comments | Examples show default/read-only roots and the explicit `read_only = false` deletion opt-in without enabling it for current roots. |

The pull request must list these documentation changes and call out the
compatibility change: all existing root configurations become non-deletable
until their owner deliberately opts a root in.

---

## Part 3 — Implementation Steps

Each step leaves the tree compiling and test collection working; steps 1–6 are
one logical commit series.

### Step 1 — Root access and library helpers

1. In `lib/imhandler/appconfig.py`, parse the optional `read_only` Boolean into
   `image_root_read_only`, defaulting to `true`. Update
   `tests/test_appconfig.py` for table, omitted, and legacy string cases.
2. In `lib/imhandler/cache.py`, add the immutable root-entry record and
   most-specific-root lookup. Update `scanner.py` and existing Django callers
   for the new entry shape without changing their read behavior.
3. Update the commented root-table examples in `etc/imhandler.conf` to show
   both `read_only = true` and the explicit `read_only = false` opt-in; do not
   opt any current root into deletion.
4. Add `invalidate(path)` to `lib/imhandler/thumbnailer.py`.
5. Add `delete_image_rows(conn, path)` to `lib/imhandler/db.py` (delete the
   image's memberships and `Images` rows, collapse affected clusters below two
   members, and commit atomically).
6. Verify: `python3 -m py_compile lib/imhandler/*.py` and
   `python3 -m unittest tests.test_appconfig`.

### Step 2 — New views

1. In `lib/imhandler/djview/__init__.py`:
   - add `_can_delete()` and the strict deletion-path helper (per 2.7–2.8);
   - add `file_info` (per 2.2) and `delete_image` (per 2.3) to
     `ImageHandlerViewSet`;
   - add stable open-file snapshot generation, signed ticket handling, and
     bounded one-use confirmation nonce state;
   - annotate cluster and Similar template context with authorization and
     root-access state;
   - delete `mark_toggle`, `deletion_list_download`, `deletion_list_clear`;
   - remove `deletion_list` session reads and the `marked` /
     `deletion_count` context values from `cluster_detail` and `similar`.
2. Update `lib/imhandler/djview/views.py` exports and
   `lib/imhandler/djview/urls.py` patterns (per 2.6).
3. In the same change, replace the removed routes in `tests/test_djview.py` so
   importing the test module never references deleted methods.
4. If either host needs a policy other than authenticated staff, configure and
   document its `IMHANDLER_DELETE_AUTHORIZER` now.
5. Verify: `python3 -m py_compile lib/imhandler/djview/*.py`, then
   `cd llime && ./manage.py check` and
   `cd ../qat/knip && ./manage.py check`.

### Step 3 — Confirmation dialog include

1. Create
   `lib/imhandler/djview/templates/image_handler/_delete_modal.html` with the
   modal markup, CSS, `{% csrf_token %}`, and the `imhDeleteConfirm()` JS
   (per 2.4). It references URL names `image_handler:file_info`,
   and `image_handler:delete_image`; confirmation thumbnails come from the
   verified snapshot response, not `image_handler:thumb`. Include the pending
   state, fetch-error handling, keyboard focus behavior, and read-only state.

### Step 4 — Template integration

1. `cluster_detail.html`: swap Mark → Delete for authorized writable members,
   show read-only/unavailable states otherwise, include the modal, wire
   `onDeleted` row and `MEMBERS` removal, repair remaining lightbox indexes, and
   navigate to `back_url` at one remaining member. Delete deletion-bar markup,
   mark JS, and related CSS (per 2.5).
2. `similar.html`: apply the same authorization/root states to the focal and
   closest panels; add deleted-state replacements; remove deletion bar, mark
   JS, and related CSS (per 2.5).
3. Verify template discovery with `cd llime && ./manage.py check`, then run:

   ```sh
   rg -n "mark_toggle|deletion_list|deletion-list|del-bar" \
       lib/imhandler llime ../qat/knip tests
   ```

   It must return nothing.

### Step 5 — Automated tests

1. Add endpoint tests using Django's test client with CSRF checks enabled:
   unauthorized → 403; GET deletion → 405; missing CSRF → 403; invalid,
   expired, and consumed tickets → 400/409 as specified. Test strict path
   rejection through `file_info` for outside-root, non-image, relative, and
   symlinked paths; missing images produce the specified non-deletable state.
2. Test that legacy string roots and table roots without `read_only` are
   read-only, return no confirmation ticket, and cannot mutate source, DB, or
   thumbnails. Every deletion-success test must explicitly configure
   `read_only = false`.
3. Test `file_info` for writable, read-only, missing, unstable, undecodable, and
   unauthorized states. Assert `Cache-Control: no-store`; exact canonical path,
   timezone-qualified mtime and `mtime_ns`; dimensions and inline thumbnail
   derived from the same mocked/open snapshot; and a ticket only for a complete,
   stable, writable snapshot.
4. After opening confirmation, modify the file in place and assert deletion
   returns 409 without unlinking. Repeat by replacing the path with a different
   inode, changing the root to read-only, expiring the ticket, and replaying a
   consumed ticket. Assert every case requires a new snapshot and never retries
   automatically.
5. Test successful ticket-bound deletion removes the file, every DB row/membership, affected
   singleton/empty clusters, and all thumbnail sizes.
6. Inject independent DB and thumbnail cleanup failures and assert that the
   response still succeeds after unlink, the other cleanup still runs, and the
   failure is logged.
7. Render both templates and assert authorized writable images have Delete,
   read-only images do not, and no removed URL name or deletion-list markup is
   present.
8. Run `python3 -m unittest discover -s tests`.

### Step 6 — Documentation

1. `specs/imhandler-django-man.md`: rewrite "Deletion workflow" — Delete
   button, confirmation dialog contents (thumbnail, path, date, dimensions),
   snapshot/ticket correctness, authorization, default read-only roots, explicit
   writable opt-in, immediate removal, and DB/thumbnail cleanup; update the
   contact-sheet and Similar page sections that mention Mark.
2. `specs/imhandler-specs.md`: replace `mark_toggle` /
   `deletion_list_download` / `deletion_list_clear` in the view table with
   `file_info` / `delete_image` (documenting request/response shapes from
   2.2–2.3); delete the `deletion_list` session-state section; document
   `image_root_read_only` and its `true` default, root entries, snapshot and
   ticket schemas, anti-replay session state, `thumbnailer.invalidate`, and
   `db.delete_image_rows`.
3. `specs/imhandler-django-impl.md`: update the view exports, URL table, and
   session-key table; describe authorization, root matching, stable snapshot and
   ticket revalidation, no-store responses, and the `_delete_modal.html`
   include.
4. `specs/imhandler-overview.md`: update the Delete workflow and root
   configuration.
5. `specs/imhandler-goals.md`: replace the deletion-list reference with the
   immediate confirmed-delete capability.
6. `specs/imhandler-imh-man.md`: document the root-table syntax, its default of
   `read_only = true`, the explicit `false` opt-in, and clarify that the flag
   controls web source deletion, not cache-maintenance CLI commands.
7. Check every entry in the specification change record at 2.9 against the
   resulting diff and include that record in the pull-request description.
8. Run the following; it must return no stale deferred-workflow references:

   ```sh
   rg -n "deletion list|deletion_list|delete\.sh|mark_toggle|deletion-list|Mark button|marked for deletion" \
       . ../qat/knip -g '!UPGRADE.md'
   ```

### Step 7 — Manual verification

With `cd llime && ./start-server`:

1. In a disposable test configuration, explicitly set one root to
   `read_only = false`; no omitted or legacy root is writable by default.
2. **Cluster contact sheet**: Delete a member → dialog shows correct
   thumbnail, full path, date, dimensions → Cancel leaves everything intact;
   confirm → row disappears, file gone from disk, its `Images` /
   `ClusterMembership` rows gone (`sqlite3` spot check), its
   `cache_dir/thumbs/` files gone.
3. **Deleting down to one member**: delete one member of a two-member cluster →
   the DB cluster and its remaining membership are gone and the browser
   navigates to Compare. Return to another cluster and confirm lightbox clicks
   and previous/next navigation still select the correct image after deleting a
   middle row.
4. **Similar page**: delete the closest match → panel shows "Deleted";
   delete the focal image → "Image deleted" notice with a working back-link.
5. **Snapshot correctness**: open confirmation, record the displayed path,
   exact date, dimensions, and thumbnail, then replace or modify the source
   before clicking Delete. The request returns the changed-file state, deletes
   nothing, fetches a new internally consistent snapshot, and requires another
   explicit confirmation. Repeat with an expired ticket and a replayed ticket.
6. **Missing/unstable file**: open the dialog for a file already removed via
   the shell, and for a file being actively rewritten → no Delete action is
   offered until one stable, complete snapshot can be produced.
7. **Default read-only behavior**: omit `read_only` from a table root and use a
   legacy string root; both remain browsable and embeddable, display no Delete
   action, issue no confirmation ticket, and reject direct deletion without
   changing source, DB, or thumbnail files.
8. **Authorization**: anonymous/non-authorized requests see no Delete actions
   and both endpoints return 403; the configured authorized identity can open
   the dialog and delete.
9. **Dialog behavior**: `Escape` / outside click close before confirmation and
   do not affect the lightbox. While deletion is in flight, Cancel, `Escape`,
   and outside clicks do nothing; on an injected failure the dialog remains and
   becomes dismissible after showing the error.
10. **Security**: `delete/` with GET → 405; invalid/expired ticket → 400/409;
    `file-info/` with outside-root, unsupported-extension, relative, and symlink
    paths → 400; no CSRF token on deletion → 403.
11. Restore the test root to read-only, then confirm the second consumer
    (`../qat/knip`) still passes
   `./manage.py check` and its imhandler pages render.

### Rollback

The change is confined to `lib/imhandler`, `etc/imhandler.conf`, tests, specs,
and any host setting that selects a custom authorizer. Reverting the commit
series restores the deferred model. Stale `deletion_list` session keys from
before the upgrade are inert in both directions.

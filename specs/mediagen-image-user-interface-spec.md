# Image Generation User Interface Specification

[TOC]

## Implementation Status

**Implemented.**

### Note on Unified Gallery

The image and video galleries, archives, and category systems are unified. See [mediagen-unified-gallery-spec.md](mediagen-unified-gallery-spec.md) for the complete gallery and archive specification. This document focuses on image-generation-specific features (creator, notes/tags, upscaling, editing).

| File | Role |
|------|------|
| `hty7/llemon/mediagen/__init__.py` + `hty7/llemon/mediagen/imagegen/__init__.py` | Shared mediagen config loading plus image-specific notes/tag accessors; populate `_config` at `init()` |
| `hty7/llemon/core/notes_db.py` | General SQLite store: `open_notes_db()`, `get_note()`, `set_note()`, `get_note_tags()`, `set_note_tags()` |
| `lib/llemon_djview/imagegen.py` | Django view: exposes notes/tags via `model_note` endpoint; passes `available_tags` and `active_notes_slot` to template |
| `~/src/hty7/python3/prj/llemon/mediagen/notes.json` | Example/default notes configuration |

---

## 1. Purpose

The notes feature provides a per-model freetext note and a set of tristate tag
checkboxes stored in the image-generation SQLite database. Each tag has three states:
not tested (indeterminate), yes (checked), no (unchecked). Both the note and
the tags are saved automatically (on textarea blur and on tag click). Notes are
scoped to a `provider:model` key, with an optional slot suffix for
multi-deployment isolation.

---

## 2. Configuration

### 2.1 Notes selector (`llemon.conf`)

The active notes slot is configured via `notes_selector` in the
`[{variant}.llemon.mediagen]` section of `llemon.conf`.  The `.local` overlay
can override it to select a different notes set without changing the base file.

| Value | Behaviour |
|-------|-----------|
| `"default"` or absent | Default slot — DB keys are `provider:model` |
| any other string `S` | Named slot — DB keys are `provider:model:S` |

### 2.2 `notes.json`

`notes.json` is an optional JSON file discovered in each directory listed in
`description_dirs` and `extra_dirs`. All files found are merged. The `//` key
is reserved for comments and is ignored.

```json
{
    "tags": ["celebrity faces", ...],
    "reverse-tags": ["block"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tags` | `list[str]` | no | Standard visible tag vocabulary. Selecting one in the filter shows only models that have it set to true. |
| `reverse-tags` | `list[str]` | no | Inverted-filter tags. Selecting one *excludes* models that have it set to true. These are binary (true/false only, no indeterminate state). |

`notes.json` controls tag visibility/editability in the current UI and
reverse-filter semantics; it does not define tag existence in the notes
database. Removing a tag from `notes.json` hides it from the current UI but
does not delete stored tag state.

`_load_notes(description_dirs)` accumulates both lists across all files.
`reverse-tags` are processed before `tags` within each file so that if a name
appears in both, the reverse definition wins. All entries from both lists appear
in `get_tags()`; `get_reverse_tags()` returns only the inverted-semantics
subset. Each tag string is appended once (first-seen order preserved; duplicates
dropped). Files that cannot be opened or parsed are silently skipped.

`block` is included in the default `reverse-tags` in
`~/src/hty7/python3/prj/llemon/mediagen/notes.json`. Selecting it in the creator filter excludes
models tagged `block: true`.

---

## 3. Package API

```python
def get_tags() -> list[str]:
    """Return the merged visible tag vocabulary from notes.json files."""

def stored_tag_names() -> list[str]:
    """Return all tag names currently stored in the notes database."""

def get_reverse_tags() -> list[str]:
    """Return tags that invert filter semantics: selecting them excludes matching models."""

def get_notes_slot() -> str:
    """Return the configured notes slot identifier, or '' for the default slot.

    'default' in the config is treated as the default slot (empty string).
    """

def get_notes_dir() -> str:
    """Return the configured notes directory, falling back to {media_dir}/notes."""
```

`get_tags()`, `get_reverse_tags()`, `get_notes_slot()`, and `get_notes_dir()`
read from `_config`, which is populated by `init()`. `stored_tag_names()` reads
the notes database. List-returning functions return copies; callers should not
mutate the results.

---

## 4. Database Schema

`hty7.llemon.core.notes_db` manages a SQLite database at
`<notes_dir>/notes.db`, where `notes_dir` is the value returned by
the image-generation backend's `get_notes_dir()`. The helper is package-neutral; any other
consumer must pass its own notes directory.

### 4.1 Table

```sql
CREATE TABLE IF NOT EXISTS model_notes (
    key        TEXT PRIMARY KEY,
    notes      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '[]'
)
```

`tags` stores a JSON object `{"tag": true/false}`. Keys present have been
explicitly set by the user: `true` = yes (applies), `false` = no (does not
apply). Tags absent from the object have not been tested. The column is added
to existing databases with a best-effort `ALTER TABLE ... ADD COLUMN` at
`open_notes_db()` time; the `OperationalError` raised when the column already
exists is silently caught.

Older rows that stored a JSON list are read as `true` for every string in the
list.

### 4.2 Key Composition

The slot applies **only to the freetext note**. Tag states are always stored
under the bare `provider:model` key so they are shared across all slots.

| Data | Condition | Key format |
|------|-----------|-----------|
| Freetext note | No slot configured | `provider:model` |
| Freetext note | Slot `S` configured | `provider:model:S` |
| Tag states | Always | `provider:model` |

The view constructs `_note_key(provider, model_id)` (slotted) and
`_tags_key(provider, model_id)` (always bare) using the slot returned by
`get_notes_slot()` at request time (not at `init()` time, so a reload picks
up configuration changes without restarting the process).

### 4.3 Functions

```python
def open_notes_db(notes_dir: str) -> sqlite3.Connection:
    """Open (or create) the notes database and ensure the schema is current."""

def get_note(conn, key: str) -> str:
    """Return the freetext note for key, or '' if absent."""

def set_note(conn, key: str, text: str) -> None:
    """Upsert the freetext note for key."""

def get_note_tags(conn, key: str) -> dict[str, bool]:
    """Return explicit tag states for key, or {} if absent or unparseable.

    True = yes (applies), False = no (does not apply).
    Tags absent from the dict have not been tested.
    """

def set_note_tags(conn, key: str, tags: dict[str, bool]) -> None:
    """Upsert explicit tag states for key."""
```

`set_note` and `set_note_tags` each write an independent `updated_at`
timestamp. They do not update each other's column — callers must call both
if updating both in one request.

---

## 5. Django View Integration

### 5.0 Constructor

The deployed Django front ends include `llemon_djview.urls` at `/llemon/`.
That shared URL module instantiates `LLemonMediaViewSet('llemon_image',
'llemon', base_nav=..., nav=...)` and exposes the Image Creator as the
`image_creator` page inside the combined Media app. `LLemonImageGenViewSet`
remains available for direct reuse, but host projects should not carry local
view/URL wrappers for the deployed LLemon UI.

| Parameter | Purpose |
|-----------|---------|
| `base_nav` | Left-side navbar items (list of `{'name': str, 'url': str}` dicts) |
| `nav` | Right-side navbar items prepended before the section-specific links on every page; optional |

The section-specific right-side links are Image Creator, Video Creator,
Gallery and Archive. They are appended after any items supplied via
`nav`.

### 5.1 Template Context

The Image Creator view passes the following to `image.html`:

| Key | Value |
|-----|-------|
| `available_tags` | `get_tags()` — list of currently visible/editable tag label strings |
| `active_notes_slot` | `get_notes_slot()` — slot identifier or `''` |
| `picker_images` | List of `{fname, url, thumb_url}` dicts for the gallery image picker |

### 5.2 `model_note` Endpoint

**GET** `?provider=P&model=M`

Returns `{'notes': str, 'tags': dict[str, bool]}`.

`tags` maps each explicitly-set tag to its state (`true` = yes, `false` = no).
Tags absent from the dict have not been tested.

**POST** body `{'provider': P, 'model': M, 'notes': str, 'tags': dict[str, bool]}`

Saves note text and tag states. The submitted dict is filtered to the known
vocabulary (`get_tags()`), but any tags already stored for the key that are not
in the current vocabulary are preserved unchanged (tags set by a deployment
with a different `notes.json` are not destroyed).
Returns `{'ok': true, 'tags': dict[str, bool]}` on success, where `tags` is the
merged stored tag state after unknown stored tags have been preserved.

---

## 6. Error States

| Condition | Behaviour |
|-----------|-----------|
| `notes_dir` not configured | GET/POST returns `{'error': 'notes_dir not configured'}` with HTTP 500 |
| DB open or query error | GET/POST returns `{'error': str(e)}` with HTTP 500 |
| Missing `provider` or `model` in GET | Returns `{'error': 'provider and model are required'}` with HTTP 400 |

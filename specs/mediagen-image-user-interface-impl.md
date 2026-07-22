# Imagegen Notes Implementation Notes

Implementation decisions for `hty7/llemon/core/notes_db.py`,
`hty7/llemon/mediagen/__init__.py` / `hty7/llemon/mediagen/imagegen/__init__.py`
(`_load_notes`, `get_tags`, `get_notes_slot`),
and the notes/tags handling in `lib/llemon_djview/imagegen.py`.
The user-facing media UI contract is in
`mediagen-image-user-interface-spec.md`; the notes data is loaded from the
shared `[*.llemon.mediagen]` config. In Grove's Django deployment,
`lib/llemon_djview` inherits the base LLemon config from `~/etc/llemon.conf`
and overlays Grove-local media UI values from `etc/llemon_djview.conf`.

`core.notes_db` is a general utility. It does not own a global database path;
the image-generation backend passes the path from `[*.llemon.mediagen].notes_dir`, and any other
package that uses the helper must pass its own notes directory.

---

## 1. `_load_notes()` as a Separate Function

Notes loading mirrors the existing `_load_quirks()` and `_load_parameters()`
pattern: a dedicated loader reads from the same `description_dirs`, merges
across files, and populates `_config` at `init()` time. Keeping it separate
makes each concern independently testable and avoids entangling the notes
merge rules with quirks logic.

`_load_notes()` only handles `tags`. The slot identifier is no longer read
from `notes.json`; it comes from `notes_selector` in `[*.llemon.mediagen]`
instead.

---

## 3. Schema Migration with `ALTER TABLE ADD COLUMN`

The `tags` column was added to an existing table rather than a new schema
version. `open_notes_db()` always attempts the `ALTER TABLE ADD COLUMN` and
silently catches `sqlite3.OperationalError` when the column already exists.
This is simpler than a schema version table for a single additive change, and
the idempotent probe has negligible cost.

The column stores a JSON object `{"tag": true/false}` rather than the original
array format. `get_note_tags()` accepts dict-format rows directly. Old
array-format rows are interpreted as `true` for each string in the array.

---

## 4. Unknown Tag Preservation in the Django View

When saving tags, the view fetches the currently stored dict, splits it into
`unknown` (keys not in the current vocabulary) and the submitted values for
known keys, then stores `{**unknown, **known_submitted}`. Tags written by a
deployment with a different `notes.json` vocabulary are opaque to this UI but
are not destroyed.

The save response returns the merged stored tag dict. The browser model-tag
cache uses that returned value so tags hidden by the current `notes.json` do
not appear to disappear during the current page session.

`--list-tags` reads tag names from the notes database, not from `notes.json`.
`notes.json` controls UI visibility/editability and reverse-filter semantics
only; tag existence is the set of names stored in the database until explicitly
removed with `--delete-tag`.

---

## 5. Auto-Save on Blur and Tag Click

The original UI had an explicit Save button. The button was removed in favour
of auto-save on textarea blur and on tag click. This eliminates the common
mistake of editing the note or toggling a tag and then navigating away without
saving. The `notesStatus` span still shows `Saving…` / `Saved.` / error
feedback so the user knows saves are happening.

## 6. Tristate Checkboxes

Each tag checkbox has three states: not tested (indeterminate), yes (checked),
no (unchecked). The cycle on click is: not tested → yes → no → not tested.

**Double-click problem.** Checkboxes inside `<label>` elements receive two
click events per user action: one from the direct click and one synthesized by
the label. The fix is `pointer-events: none` on the checkbox, which makes the
checkbox transparent to mouse events. All clicks land on the enclosing label
instead, firing exactly once.

**Implementation.** The click handler is bound to each `<label>` in
`#notes-tags`. It calls `e.preventDefault()` (preventing the label's default
toggle behaviour), reads the current state from `cb._tristate` (null = not
tested, true = yes, false = no), advances it, then calls `_setTristate()` to
apply `checked` and `indeterminate` properties and store the new `_tristate`
value.

`loadNote()` initialises every checkbox to `null` (indeterminate) before
applying the stored dict. `saveNote()` collects only explicitly-set tags
(where `_tristate !== null`) into a `{tag: bool}` dict for the POST body.

## 7. Creator Model Filter Tags

The creator page renders a second set of tag checkboxes next to the model
dropdown. These checkboxes filter the dropdown and do not edit tag state.
Ordinary selected tags are permissive: a model is hidden only when that tag is
explicitly `false`; `true` and absent both remain visible.

`block` is a special filter tag. It is checked on initial render and whenever
the filter controls are reset. While checked, it hides models with `block:
true`; models with `block: false` or no `block` state remain visible.

The Django view's model-tag-state payload is read from the notes database.
There is no `block` quirk path; setting or clearing a model's blocked state is
handled through the `block` tag.

---

## 8. Notes Slot from Config

The notes slot identifier is read from `notes_selector` in
`[{variant}.llemon.mediagen]` at `init()` time and stored in `_config`. The
value `"default"` (and the absent case) both map to the empty string so that
`_notes_key()` produces `provider:model` — identical to the pre-slot behaviour.
Any other value `S` produces `provider:model:S`.

The `.local` config overlay can override `notes_selector` to direct a given
deployment to a different notes set without modifying the base file.

---

## 9. Creator Type Selector

The image creator page supports three operation types via a **Type** dropdown
selector positioned inline next to the Provider dropdown. Selecting a type
controls form visibility and submission routing:

- **Normal**: text-to-image generation (default, existing flow)
- **Upscale**: upscale an uploaded image with scale/enhancement options
- **Edit**: edit an uploaded image with model and aspect-ratio selection

The Type selector is shown only when both `upscale_url` and/or `edit_image_url`
are registered in the URLconf.

### Type Selector Visibility

The Type selector HTML is conditionally rendered only when at least one of
`upscale_url` or `edit_image_url` is in the template context:

```django
{% if upscale_url or edit_image_url %}
<label for="image-type">Type</label>
<select id="image-type">
  <option value="normal">Normal</option>
  {% if upscale_url %}<option value="upscale">Upscale</option>{% endif %}
  {% if edit_image_url %}<option value="edit">Edit</option>{% endif %}
</select>
{% endif %}
```

This allows deployments without upscale/edit endpoints to not show the selector.

### Form Section Visibility by Type

The `switchType(type)` function manages visibility of form sections:

| Section | Normal | Upscale | Edit |
|---------|--------|---------|------|
| Model dropdown | show | hide | hide |
| Aspect ratio/Size/Format/Style row | show | hide | hide |
| Temperature | show | hide | hide |
| Provider options section | show | hide | hide |
| Source image selector | hide | show | show |
| Upscale options panel | hide | show | hide |
| Edit options panel | hide | hide | show |
| Model notes + tags | show | show | show |

Model notes and tag checkboxes remain visible in all types, allowing users to
review and edit per-model metadata across operation types.

The prompt textarea label is relabeled based on type: "Prompt" for normal and
upscale, "Instructions" for edit. The submit button label changes to match:
"Generate", "Upscale", "Edit".

### Source Image Selector

For upscale and edit modes, a source-image selector appears in the right column
at the bottom. It contains:

- A **Choose…** button that opens a modal picker
- A label showing the selected filename
- A **Clear** button (visible only when an image is selected)

Selected state is persisted in data attributes on the `#source-image-section`
DOM element: `data-selectedFname` (filename) and `data-selectedUrl` (full URL).
This decouples state from global variables.

The image picker modal displays a grid of 120×120 mini-thumbnails from the
gallery, rendered using `appendImageThumb()` (matching the video creator
pattern). Selected images show a blue border. Clicking an image or the Close
button dismisses the modal and updates the source-image label. Input files
must come from the gallery; to use a source dir image, copy it to the gallery
first via the Source Dirs browser.

### Form Submission Routing

The form's submit handler checks the Type selector value and routes to the
appropriate endpoint:

- **normal**: POST to `generateUrl` (existing flow)
- **upscale**: POST to `upscaleUrl` with JSON body
  `{provider, fname, scale, enhance, ...}`
- **edit**: POST to `editImageUrl` with JSON body
  `{provider, fname, model, aspect_ratio, prompt}` plus `image_size` when the
  provider's edit path accepts an explicit size (OpenRouter)

Both upscale and edit receive streaming NDJSON responses (same `readGenerateStream()`
pattern as normal generation) and display results using the existing
image-result rendering code.

Every action POST (`generate`, `upscale`, and `edit`) must contain the provider
currently selected in the form. The server returns HTTP 400 with
`provider is required` when it is absent or empty; action endpoints never
select the package default provider.

### Generation Metadata and Prompt Enhancement

`_generate_result()` reads `generated_prompt` and `prompt_enhancement` from
the backend result (present when a mediagen prompt-enhancement selector
matched the request; see the LLemon `mediagen-image-spec.md`). Both fields
are passed to the metadata writers and, when a generated prompt is present,
the client-side canonical EXIF/sidecar writer
(`write_image_generation_exif_with_sidecar_fallback`) is used even for a
backend that did not request server-side embedding, so the original and
generated prompts are both represented in the embedded `generationParams`.
The `prompt` value everywhere remains the original user prompt. The summary
returned to the creator gains a `Generated prompt` line via
`image_generation_summary_lines()`, and the JSON response includes a
`generated_prompt` key when one exists. Unenhanced generations omit all of
these additions. Enhancement failures are terminal: the backend returns a
`prompt_enhance_`-prefixed structured error before any image provider
request, and the view reports it like any other generation error (the Django
path performs no outer retries).

### Upscale Options

When Type is upscale, a panel shows:

- **Scale** dropdown: 2×, 3×, 4×, 1× (enhance only)
- **Enhance** checkbox: when checked, reveals prompt/creativity/replication fields

The enhance sub-options are omitted from the POST body when enhance is unchecked.

### Edit Options

When Type is edit, a panel shows:

- **Model** dropdown: edit models from the `edit_models` context variable.
  The list comes only from live discovery (`list_edit_models()`). Discovery
  failure or an empty result produces an empty list, reports that image editing
  is unavailable through `edit_models_warning`, and disables the Edit action.
  There is no static or default-model fallback.
- **Aspect ratio** dropdown: exactly the provider's `edit_aspect_ratios`.
  There is no empty "(source)" choice. Venice includes `auto`
  (displayed as "auto (source)"), which preserves the source ratio;
  OpenRouter has no source-preserving ratio, so a concrete ratio is always
  selected and submitted. `default_edit_aspect_ratio` selects `auto` when
  available, otherwise the provider's default generation ratio.
- **Size** dropdown: shown only when `edit_image_sizes` is non-empty
  (OpenRouter). For Venice single-image edits the control is replaced by a
  note explaining that output size is determined by the source image.

The submitted body always contains `provider`, the explicitly selected edit
`model`, and `aspect_ratio`; `image_size` is included only while the size
control is visible. With no discovered edit model, the client submits nothing.

Server-side, `_do_edit_image()` re-validates everything: the edit model must
be explicitly present and in the discovered list, the aspect ratio must be one of
`edit_aspect_ratios` (with `auto` supplied as the default when the provider
offers it, and a 400 requiring an explicit fixed ratio when it does not),
and `image_size` is rejected with a provider-appropriate message when the
provider does not accept one, or validated against `edit_image_sizes` and
defaulted when it does. `_edit_result()` forwards `image_size` to
`backend.edit()` only when set and records it in the operation sidecar. An
empty discovered list returns HTTP 400 before a backend is constructed.

### Backend Context Additions

The `image_creator()` view adds to the template context:

- `upscale_url`: URL path or `None`
- `edit_image_url`: URL path or `None`
- `picker_images`: list of dicts with `fname` and `thumb_url` from gallery
- `supports_edit`: effective edit availability; false when the backend lacks
  editing or live discovery yielded no valid model
- `edit_models`: live-discovered edit model identifiers; never a static fallback
- `edit_models_warning`: discovery-failure/empty-catalog message or `None`
- `default_edit_model`: first discovered model for initial UI selection, or
  `''` when editing is unavailable; it is not a server request fallback
- `edit_aspect_ratios`: the provider's edit ratios (no empty entry)
- `default_edit_aspect_ratio`: `auto` when offered, else the default ratio
- `edit_image_sizes`: permitted edit sizes (empty when size is automatic)
- `default_edit_image_size`: default selected edit size (`''` when automatic)

These edit keys are produced by the module-level `_edit_metadata()` helper,
which caches live edit-model discovery for five minutes per provider so page
renders and edit requests do not each pay a catalog fetch. The same keys are
included in the `models_json` response so a provider switch updates the edit
controls without a page reload. Unit/render tests replace `_edit_metadata()`
or the backend catalog method with deterministic doubles and never contact a
live provider.

The `_gallery_picker_items()` private method scans the gallery directory for
image files (extensions: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`) and returns
a sorted list of dicts with `fname` and `thumb_url`. Does not call
`_ensure_thumbnail()` on page load for performance; uses pre-existing thumbnails.

### Gallery Cleanup

The upscale and edit buttons that previously appeared on the gallery's image
detail panel have been removed, eliminating redundancy. All upscale/edit
operations now flow exclusively through the creator's Type selector.

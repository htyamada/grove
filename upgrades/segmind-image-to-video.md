# UPGRADE: Segmind Image-to-Video Private-Source Transport

Video Creator already wires Segmind's `wan-2.2-i2v-fast` image-to-video
model: it shows a start-image control and forwards the value as `image_url`.
That control is a plain public-URL text field rather than Grove's usual
Gallery/Source-Dirs image picker. This file tracks the work left to let
that picker be used for this model's start image.

The gap and constraint are recorded in
[`specs/mediagen-video-user-interface-spec.md`](../specs/mediagen-video-user-interface-spec.md),
"Segmind image-to-video integration" — that section is authoritative for
the *contract*; this file tracks the work and, below, the concrete Grove
code this needs to touch.

---

## Status

**The hty7-side blocker is resolved (2026-09-04).** LLemon now has a video
`provider_upload` transport: a `data:` `image_url` for `wan-2.2-i2v-fast`
uploads through Segmind's own asset-upload endpoint and submits the
returned hosted reference, live-validated end to end. This was the first
of Part 1's two either/or options — the second (getting LLemon to accept
`data:` input directly) was not pursued and is no longer needed.

Reference, in `~/src/hty7`:

- `python3/lib/hty7/llemon/mediagen/videogen/segmind.py` — `_generate()`
  gained a keyword-only `accept_data_handling_warnings: bool = False`
  parameter (mirrors `imagegen.segmind.edit()`'s identical contract). A
  `data:` `image_url` is accepted only when both (a) the model is in
  `UPLOAD_VERIFIED_I2V_MODELS` (currently `{'wan-2.2-i2v-fast'}` — check
  this constant before assuming any other Segmind i2v model works the same
  way) and (b) `accept_data_handling_warnings=True` is passed; otherwise it
  fails closed (`invalid_request` if the model isn't verified,
  `warning_not_accepted` if it is but the flag wasn't set).
- `python3/lib/hty7/llemon/mediagen/videogen/__init__.py` — `model_presentation()`'s
  `segmind` branch now returns `required_backend_transports`,
  `available_backend_transports`, and `transport_warnings` for i2v models
  (empty/absent for every other provider's branch — check with `.get(...)`,
  not direct indexing). `available_backend_transports` containing
  `'provider_upload'` is the live signal that a given model's picker path
  is actually usable today; don't hardcode `wan-2.2-i2v-fast`, read this.
- `python3/prj/llemon/specs/mediagen-video-segmind-spec.md`, "Task 9 Step
  3: provider_upload for image_url" and "Task 9 Step 3 live validation" —
  full design and evidence
  (`python3/prj/llemon/validation/segmind/video-i2v-upload-probe-2026-09-04/result-20260904T045303Z.json`).
- **Known caveat from the live-validation run, not yet explained or
  fixed:** the output came back square (624×624) instead of the requested
  16:9 landscape shape, despite `_generate_impl()` always sending
  `aspect_ratio` explicitly on the wire (same code Step 1/Step 2 already
  validated). This looks like Segmind's `wan-2.2-i2v-fast` not reliably
  honoring `aspect_ratio` for image-to-video mode specifically — provider
  behavior, not a LLemon defect, and not chased down. If Grove's video
  preview/output handling assumes the requested aspect ratio, this model
  may not reliably deliver it; don't spend time re-debugging this as a
  Grove bug before checking here first.

## Part 1 — Requirements

### 1.1 Grove has no data-handling-warning consent UI yet (blocking precondition) — RESOLVED

**Resolved (2026-09-04).** Grove now collects `accept_data_handling_warnings`
consent, generically, for image editing. Full design, code references, and
test coverage are tracked in
[`upgrades/data-handling-warning-consent.md`](data-handling-warning-consent.md),
not here — that file is now authoritative for this prerequisite. The gist:
`_operation_state()`'s eligible/enabled split keeps a warned model
selectable while gating only the edit action; a new
`_resolved_edit_warning_reason()`/`resolvedEditWarning()` pair (Python/JS)
decides consent from the roles actually assigned to a request, not a
schema-wide aggregate; and `image.html` gained a persistent
`#edit-consent-row` (verbatim warning text + checkbox) distinct from
`#model-info-notices`.

This unblocks Segmind image editing's `qwen-image-edit` upload path today.
§1.2 below (the video start-image picker) can now proceed — it is the
remaining reason this file exists.

### 1.2 Swap the manual URL field for the picker — DONE

The hty7-dependent semantics this needs — exactly when the availability
gate opens, when `accept_data_handling_warnings` may be set, and what the
new upload-specific error types mean — are designed in
`specs/mediagen-video-user-interface-spec.md`, "Segmind image-to-video
integration", "Design: the hty7-dependent contract". What follows here is
the mechanical "where in the code" this touches, not that contract.

**Implemented and reviewed (2026-09-04, codex/gpt review, no issues found;
committed as `2e60d03`).** **Decided:** Segmind's start-image control follows Venice's own UI pattern
exactly — **picker-only**, no manual-URL fallback. The picker swap and its
consent checkbox ship together as one change, not staged separately: the
picker only ever supplies private Grove media URLs, which
`_data_reference_for_api` always turns into a `data:` `image_url` — the
exact transport `wan-2.2-i2v-fast` requires consent for — so wiring the
picker with no consent path would make every picker selection 400
(`warning_not_accepted`), a regression versus today's field working for at
least public URLs.

**What already exists to build on:**

- Venice's start-image picker in `video.html` is the exact pattern to
  copy: shared `#image-picker` modal (333-352), row markup
  `#start-image-row` (296-303), state `startImageUrl`/`startImageFname`
  (381), `openImagePicker('start')` (1254) → `handlePickerWrapClick`'s
  `pickerTarget === 'start'` branch (1283-1296) → `updateStartImageDisplay()`
  (428-431) → read into the submit body at
  `if (startImageUrl) body.image_url = startImageUrl;` (1411-1414, inside
  the Venice provider branch).
- `#start-image-row` is currently shown for no provider but Venice/
  OpenRouter: `updateImageChoiceVisibility()` (904-942) branches on
  `currentProvider`, and its `else` branch (919-924, today's Segmind case)
  forces `startVisible = false` unconditionally. This is *why* Segmind
  needed its own field, not a gap in `allows_start_image` itself (Segmind's
  presentation already sets `allows_start_image: true`, already read by
  today's `showSegmindStartImage`, line 866-868).
- Current Segmind field to remove: `#segmind-start-image-row` /
  `#segmind_start_image_url` (273-276); shown/hidden via
  `showSegmindStartImage` inside `updateProviderOptionVisibility()`
  (830, 866-868, 882, 893); read into the submit body at 1482-1487;
  cleared in `resetForm()` (1060); special-cased once more in
  `applyUrlPrefill()` (970-974).
- The transport data the consent check needs is already on the wire:
  `_model_options()` (`videogen.py:229-259`) sets
  `capabilities['presentation'] = model_presentation(row['id'], provider,
  api, capabilities=capabilities)` with no filtering, so
  `required_backend_transports`/`available_backend_transports`/
  `transport_warnings` are already present in `MODEL_OPTIONS` for Segmind
  models today, and client-side `modelPresentation(modelId)` (544-548)
  already returns the whole dict. No server→browser JSON plumbing is
  needed — only new code that reads these fields.
- `_source_kind_usability()` (`imagegen.py:100-129`) is the reference shape
  for reading the three transport dicts. Video's presentation is a flat
  dict (no per-role scopes), so this reduces to one check against
  `source_kind='data_url'` — not the AND/OR multi-role logic
  `_operation_state()`/`_resolved_edit_warning_reason()` needed for image
  editing. `videogen.py` has no `_operation_state()`-equivalent and none is
  needed here; building one would be over-scoped for one model/one
  transport.
- Image editing's shipped consent UI
  (`upgrades/data-handling-warning-consent.md`: `image.html`'s
  `#edit-consent-row`/`refreshEditWarningState()`) is the UI/verbatim-text/
  checkbox-reset-key pattern to mirror, simplified for a single image slot.

**Planned implementation:**

Backend, `lib/llemon_djview/videogen.py`:
1. Near the top of `_generate()`, strictly parse
   `accept_data_handling_warnings` once (only a literal JSON `true` counts;
   any other non-boolean value 400s explicitly — the same
   `bool(...)`-coercion bug already caught and fixed once for image
   editing's `_do_edit_image()`).
2. In the `provider == 'segmind'` branch (691-697), after resolving
   `clean_value` through `_data_reference_for_api` (unchanged), check
   whether the result is a `data:` URL; if so, look up
   `required = presentation.get('required_backend_transports', {}).get('data_url')`,
   400 if `required` isn't in `available_backend_transports`, 400
   "requires accepting a data-handling warning" if `transport_warnings` has
   an entry for `required` and consent wasn't given, else add
   `generate_kwargs['accept_data_handling_warnings'] = True` — scoped
   inside this branch only, so it's never forwarded to Venice/OpenRouter.
3. No change needed to `_model_options()`/`model_presentation()` — the data
   already reaches the page.

Frontend, `lib/llemon_djview/templates/llemon_video/video.html`:
1. Delete the old Segmind field, its visibility wiring, its `resetForm()`
   clear, and its `applyUrlPrefill()` special case (folding Segmind into
   the existing non-Segmind branch there).
2. Add a `currentProvider === 'segmind'` branch to
   `updateImageChoiceVisibility()`. New helper
   `segmindStartImagePickerState(modelId)` returns
   `{visible, disabled, reason}`: `visible: false` when `allows_start_image`
   isn't `true` (row hidden, as today); otherwise `visible: true`, and
   **fails closed** on `disabled`/`reason` — the picker is enabled only when
   `required_backend_transports.data_url` is both present *and* included in
   `available_backend_transports`; every other case (that transport declared
   but unavailable, `required_backend_transports` present without a
   `data_url` entry, or the whole presentation missing this metadata
   entirely) is `disabled: true` with a reason (e.g. "Start-image upload
   isn't available for this model yet."). The picker being enabled requires
   a positive, declared, available signal — absent metadata is never treated
   as "must be fine." A model outside `UPLOAD_VERIFIED_I2V_MODELS`, or one
   whose presentation simply doesn't carry this data, both get a *visibly
   disabled* picker with an explanation, never a picker that's offered and
   then guaranteed to 400. `startVisible` in `updateImageChoiceVisibility()`
   comes from `.visible`; the segmind branch additionally sets
   `#start-image-btn`'s `disabled` attribute and a small note element from
   `.disabled`/`.reason` (reset/cleared for every other provider). The other
   three image flags stay `false` — i2v models only take a start image.
3. Submit path: mirror Venice's `if (startImageUrl) body.image_url =
   startImageUrl;` shape in the Segmind branch, replacing the old direct
   input read.
4. New `#segmind-consent-row` (message + checkbox), placed right after
   `#start-image-row`, mirroring `#edit-consent-row`'s markup/`#a60`
   styling.
5. New `segmindStartImageWarning()` helper mirroring
   `_source_kind_usability()`'s single-`source_kind` check against
   `startImageUrl`'s presence and the current model's presentation.
6. New `refreshSegmindConsent()`, hooked into the *single* shared place
   `startImageUrl` actually changes — the end of `updateStartImageDisplay()`
   itself (a no-op guarded by `currentProvider !== 'segmind'` for every
   other provider) — plus the provider/model switch and the checkbox's own
   `change` listener. `updateStartImageDisplay()` already runs on every
   path that can change the selected image (picker pick, `clearStartImage()`,
   and `applyUrlPrefill()`'s `?image_url=` prefill), so hooking there,
   rather than each caller individually, is what makes picking, clearing,
   and prefilling all keep the consent row in sync — the original draft of
   this plan listed only visibility/provider/checkbox changes and missed
   this, which would have left the checkbox hidden after the first pick and
   made submission fail server-side. It shows/hides the row, sets the
   message verbatim, resets the checkbox on a `(currentProvider,
   CREATOR_PRESENTATION.api, modelSel.value, warning)` key change (same
   shape as `#edit-consent-checkbox`'s reset key, for the same reason —
   model ids are only unique within a provider/API), and keeps the generate
   control disabled while warned-and-unchecked.
7. Video's submit handler: defensive guard blocking submission if the
   consent row is visible and unchecked; always send
   `accept_data_handling_warnings: !!checkbox.checked` for Segmind.

**Tests:**
- `tests/test_llemon_djview_prompt_enhance.py`: no existing test exercises
  the Segmind branch of `_generate()` at all. Add a new test class (same
  `_run_generate`-style harness as `VideoEnhancementPassthroughTests`)
  covering: public-URL submission needing no consent; private-URL
  (`data:`-resolved) submission without consent → 400; same with consent →
  dispatches with `accept_data_handling_warnings=True` reaching the fake
  backend; non-boolean `accept_data_handling_warnings` → 400; an
  unwarned model/provider succeeding with the flag omitted.
- A new or extended `tests/js/` jsdom harness (pattern:
  `tests/js/edit_images_dom_test.js`): picker swap actually removes the old
  field; consent row is warning-and-selection-aware; checkbox gates
  submission; switching model/provider resets the checkbox including a
  provider-id/message-collision case; **picking** a gallery image shows the
  consent row and message (not just selecting the model); **clearing** the
  start image hides it again; **URL-prefill** (`?image_url=`, once folded
  into the shared branch per step 1 above) also triggers the same refresh;
  and a model with `allows_start_image: true` but `provider_upload` absent
  from `available_backend_transports` renders the picker button disabled
  with the reason text, never an enabled-but-guaranteed-to-fail control.
  **Missing-metadata case:** a model with `allows_start_image: true` whose
  presentation has no `required_backend_transports`/
  `available_backend_transports` at all (not merely an empty dict) must
  still render the picker disabled — proving the check fails closed on
  absent metadata rather than defaulting to enabled.

**Verification:** `python3 -m py_compile lib/llemon_djview/*.py`;
`cd llime && ./manage.py check`; `python3 -m unittest discover -s tests -t
.`; `cd llime && ./manage.py test`; manual pass in the Video Creator
against Segmind `wan-2.2-i2v-fast` (or a stubbed `transport_warnings` row
via devtools if this environment can't reach Segmind discovery) confirming
the field is gone, the picker/consent row work, and a real submission
carries `accept_data_handling_warnings: true`.

### 1.3 Warn users about the known aspect-ratio caveat — PLANNED

**Problem:** the "Status" section above records a live-validated,
unexplained caveat: `wan-2.2-i2v-fast` image-to-video output came back
square (624×624) instead of the requested 16:9, even though Grove always
sends `aspect_ratio` explicitly on the wire. This is Segmind provider
behavior, not a Grove or hty7 defect, and investigating/fixing it is
explicitly out of scope (see below) — but today nothing tells the user
this can happen; they just get a mismatched result with no explanation.
This task only adds a static, informational warning at the point of use so
users aren't blindsided — it does not touch §1.2's picker/consent logic at
all, and ships as a purely additive change.

**Decided:** this is a Grove-only, hardcoded note (model id → message),
not data sourced from hty7. Confirmed by reading
`~/src/hty7/python3/lib/hty7/llemon/mediagen/videogen/__init__.py`'s
`model_presentation()`: there is no "known caveat"/quirk field in its
return value for any provider, and adding one would be an hty7-side
change — out of scope per this file's own "Explicitly out of scope"
section. So the note lives entirely in Grove, keyed off `(provider,
model_id)`, the same way `UPLOAD_VERIFIED_I2V_MODELS` is Grove's own
read of a live signal rather than something to hardcode against.

**What already exists to build on:**

- `_model_options()` (`lib/llemon_djview/videogen.py:229-259`) is the one
  place that builds each model's `capabilities` dict (including
  `capabilities['presentation']` from `model_presentation(...)`) for both
  the metadata-rich branch (`list_video_models_with_metadata()`, 232-247)
  and the plain-id-list fallback branch (249-259). Both branches already
  build `capabilities` as a Python dict Grove controls before it's
  serialized to JSON — a good, minimal insertion point for an
  additional Grove-only key that isn't part of hty7's `presentation`
  payload.
- `video.html`'s `modelPresentation(modelId)` (~544-552) is the existing
  pattern for a small helper that looks up `MODEL_OPTIONS` by id and reads
  one field off `capabilities`; a `modelCapabilities(modelId)` sibling
  (read `capabilities` itself rather than `capabilities.presentation`) is
  a two-line addition, not new plumbing. `selectedModelCapabilities()`
  (~648-651) already does the same read but only for the *currently
  selected* model — not reusable as-is since the new helper needs to
  work by arbitrary model id the same way `modelPresentation` does.
- `#model-description` / `#description-section` (video.html, ~78-80,
  246-248) already show a per-model text block driven purely by the
  selected model id, updated at the one call site that sets
  `modelDesc.textContent` (~762) — the natural place to add a second,
  parallel update for the new notice so there's exactly one call site to
  hook, not one per place the model can change (provider switch, model
  switch, `pageshow` restore, URL prefill).
- The `#a60` warning color is already established for
  `#model-info-notices.warning` (`image.html`) and `#segmind-consent-message`
  (`video.html`, from §1.2) — reuse it rather than inventing a new color.
- Image editing's `#model-info-notices` (`image.html`) is a *dynamic*,
  API-driven notices system (`on_model_info_notice` callbacks threaded
  through `imagegen.py`, see `grep -n on_model_info_notice
  lib/llemon_djview/imagegen.py`) that has no video equivalent at all
  (`grep -n on_model_info_notice lib/llemon_djview/videogen.py` is empty).
  Porting that whole mechanism to video would be a much larger, differently
  scoped task (dynamic notices from live LLemon API calls) and is not
  needed here — this task's note is static and keyed purely off the
  selected model id, so it doesn't need that machinery.

**Planned implementation:**

Backend, `lib/llemon_djview/videogen.py`:
1. Add a module-level constant near the other Segmind-specific constants:
   ```python
   _KNOWN_MODEL_CAVEATS: dict[tuple[str, str], str] = {
       ('segmind', 'wan-2.2-i2v-fast'): (
           'This model has been observed to ignore the requested aspect '
           'ratio in image-to-video mode and return square (1:1) output '
           'instead. This is Segmind provider behavior, not a Grove bug.'
       ),
   }
   ```
2. In `_model_options()`, in both branches, after `capabilities` is built
   and `capabilities['presentation']` is set, add:
   ```python
   caveat = _KNOWN_MODEL_CAVEATS.get((provider, row['id']))  # or model_id in the fallback branch
   if caveat:
       capabilities['known_caveat'] = caveat
   ```
   Omit the key entirely when there's no caveat, matching the codebase's
   existing `.get(...)`-not-indexing convention for optional presentation
   fields (see the "Status" section's own note about `.get(...)` on
   `required_backend_transports`/etc.) — every other model's JSON payload
   is byte-for-byte unchanged.

Frontend, `lib/llemon_djview/templates/llemon_video/video.html`:
1. New helper next to `modelPresentation()`:
   ```js
   function modelCapabilities(modelId) {
     const option = MODEL_OPTIONS.find(item => item.id === modelId);
     return (option && option.capabilities) || {};
   }
   ```
2. New markup: a `#model-caveat-notice` div inside `#description-section`
   (alongside `#model-description`), styled with the existing `#a60`
   warning color. Placed with the description rather than inside
   `#start-image-row`/`#segmind-consent-row` so it's visible as soon as the
   model is selected — before the user has picked a start image and been
   surprised by the output — and so it stays visually distinct from §1.2's
   consent row (this is informational, not a gate).
3. New `refreshModelCaveatNotice()`, called from the same place that
   currently sets `modelDesc.textContent` (~762): reads
   `modelCapabilities(modelSel.value).known_caveat`, sets the notice's
   `textContent` and visibility. One call site, mirroring how the
   description itself already updates on every model-change path (provider
   switch, model switch, `pageshow`, URL prefill) with no per-path special
   casing needed.
4. No interaction with `#generate-btn`, `startImageUrl`, or the
   `segmind-consent-*` machinery from §1.2 — purely additive and orthogonal
   to already-shipped, already-reviewed code.

**Tests:**
- New backend test (new test class in
  `tests/test_llemon_djview_prompt_enhance.py`, alongside
  `SegmindVideoStartImageConsentTests`): call `_model_options('segmind',
  'segmind')` against a fake backend whose
  `list_video_models_with_metadata()` returns rows for `wan-2.2-i2v-fast`
  and one unrelated model id, with `model_presentation` mocked; assert
  `wan-2.2-i2v-fast`'s `capabilities['known_caveat']` equals the exact
  configured string, and the unrelated model's `capabilities` dict has no
  `known_caveat` key at all (not an empty string — key must be absent).
- Extend `tests/js/video_dom_test.js`: add `known_caveat` to one fixture
  model's `capabilities` (reuse an existing model id rather than adding a
  new one, to keep the fixture set from growing unnecessarily); assert the
  notice renders with the exact text when that model is selected, and is
  hidden (empty, `display:none`, or equivalent) when switching to any
  model without the key — including re-checking one of §1.2's existing
  fixture models to confirm the two features don't interfere with each
  other.

**Verification:** `python3 -m py_compile lib/llemon_djview/*.py`;
`cd llime && ./manage.py check`; `python3 -m unittest discover -s tests -t
.`; `cd llime && ./manage.py test`; manual pass in the Video Creator
selecting Segmind `wan-2.2-i2v-fast` and confirming the notice appears
verbatim, then switching to any other model and confirming it disappears.

## Explicitly out of scope for this file

Any hty7/LLemon-side change (done, see "Status"); any Segmind
image-to-video model other than `wan-2.2-i2v-fast` (not live-validated);
investigating or fixing the aspect-ratio shape variance itself (1.3 only
surfaces it as a known caveat, it does not chase down the root cause); a
general cross-media-type redesign of Grove's data-handling-warning UI
beyond what's needed to unblock 1.1 (though building it generically rather
than video-only is recommended, per 1.1's own note); porting image
editing's dynamic `on_model_info_notice` API-driven notices system to
video (1.3's notice is static and model-id-keyed, not live API data —
porting that whole mechanism is a separate, larger task).

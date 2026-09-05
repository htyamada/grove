# UPGRADE: Data-Handling-Warning Consent UI

LLemon's `edit()`/`edit_images()` backend contract has supported a
keyword-only `accept_data_handling_warnings` flag since Task 8 Phase 5,
gating any warned backend transport (currently only Segmind's
`provider_upload`, used by `qwen-image-edit` and, later, video
image-to-video upload). Grove never collected that consent for any media
type, so a warned transport was reported unusable and unreachable through
Grove even though LLemon's backend already supported it. This file tracks
building the UI/plumbing that collects it, generically, for image
editing — the first and (as of this writing) only Grove surface with a
warned transport.

The full contract Grove must meet is specified in
`~/src/hty7/python3/prj/llemon/specs/mediagen-image-spec.md`, "Data-handling
warnings and caller acceptance" — that section is authoritative for *why*
and *what*; this file tracks the *where in Grove's code*.

---

## Status

**Implemented (2026-09-04).** Both the backend gate and the frontend
consent UI are built and tested for `llemon_image`'s multi-image edit
flow.

- `lib/llemon_djview/imagegen.py`:
  - `_operation_state()` gained a keyword-only
    `accept_data_handling_warnings: bool = False`. A warned-but-otherwise-
    usable candidate is now `(eligible=True, enabled=False, reason=...)`
    by default — selectable, but not enabled — and
    `(True, True, None)` when the flag is passed. This is a schema-level,
    assignment-blind aggregate: useful for model-dropdown annotation and
    for isolating genuine unusability, but it is *not* the per-request
    consent gate (see below).
  - New `_resolved_edit_warning_reason(edit_inputs, images)` is the
    actual per-request gate: given the images/roles a specific request
    actually assigns, it returns the verbatim warning text for whichever
    resolved transport is both usable and warned, or `None`. An optional
    role nobody assigned an image to cannot make a request need consent,
    even if that role would be warned in isolation — this is the
    assignment-aware check the schema-level aggregate cannot do.
  - `_do_edit_image()` calls `_operation_state(..., 
    accept_data_handling_warnings=True)` (flag forced) to isolate genuine
    schema unusability from the consent question, parses
    `accept_data_handling_warnings` strictly (only a literal JSON `true`
    counts; any other non-boolean value 400s explicitly rather than being
    coerced), and calls `_resolved_edit_warning_reason()` right after
    `normalize_edit_inputs()` succeeds — before any gallery file is read —
    400ing if it returns a reason and consent wasn't given. The validated
    boolean threads through to both `_edit_result()` (non-streaming) and
    `_edit_stream()` (streaming), replacing the old hardcoded `False`.
- `lib/llemon_djview/templates/llemon_image/image.html`:
  - `roleDataUrlUnusableReason()` no longer treats a warned transport as
    unusable (only genuine `unavailable_transport`/`unsupported` block a
    role) — this also fixes `currentEditImagesMaxCount()`'s picker-
    capacity cap and `_editInputsSignatureFor()`'s compatibility signature
    for free: a warned role now counts toward capacity, and a model
    switch that only changes a role's warning text (not its usability)
    no longer wipes the user's current image/role assignment.
  - `editOptionState()` no longer disables a model merely for being
    warned; new `resolvedEditWarning(option)` is the JS mirror of
    `_resolved_edit_warning_reason()`, evaluated against the actual
    `editImageRoles` assignment.
  - New UI: a persistent `#edit-consent-row` (message + checkbox) as the
    last row inside `#edit-opts`, distinct from the transient
    `#model-info-notices` area, shown only while the current
    model+assignment actually needs consent, displaying the backend's
    warning text verbatim. New `refreshEditWarningState()` computes this
    from current global state (never a stale argument) and is called from
    every place that can change it: `_applyEditMetadata()` (initial load,
    provider switch), the `edit-model-sel` change handler, the end of
    `renderEditImagesList()` (image add/remove/move/clear), the
    per-thumbnail role `<select>`, and the checkbox itself.
  - The checkbox resets to unchecked whenever the reset key — `(provider,
    api, model id, messages)` — changes; provider is part of the key
    because model ids are only unique within a provider/API, so two
    providers reusing the same id and warning text must not let consent
    carry over between them.
  - `handleEditSubmit()` always sends `accept_data_handling_warnings:
    !!checkbox.checked` and defensively refuses to submit if the row is
    visible and unchecked (mirroring this function's other pre-submit
    re-checks), so Grove cannot offer a way to submit while unchecked.
- Tests: `tests/test_image_creator_render.py` (the `_operation_state()`
  eligible/enabled split, including the two named-schema warned
  scenarios and a new `_resolved_edit_warning_reason()` suite covering
  required-role, optional-role-only-when-assigned, and ordered-schema
  cases); `tests/test_llemon_djview_prompt_enhance.py` (`_edit_result()`/
  `_edit_stream()` forwarding, `_do_edit_image()`'s strict boolean
  parsing and end-to-end warned/optional-role dispatch); `tests/js/
  edit_images_dom_test.js` (real-DOM coverage: warned roles stay
  selectable, the selection cap counts them, the consent row is
  assignment-aware, a warning-only schema change preserves a stale
  selection while a genuine usability change still clears it, and a
  provider switch resets consent even when the model id and warning text
  collide).

## Explicitly out of scope for this file

Actually wiring this into Segmind's video start-image picker
(`upgrades/segmind-image-to-video.md` §1.2 — that file's blocking
prerequisite, §1.1, is resolved by this work, but the picker wiring
itself is untouched here); any warned transport outside image editing;
any UI treatment beyond a checkbox + verbatim message (e.g. a modal or
blocking confirmation dialog — the LLemon spec explicitly says this is
not meant to be an interactive confirmation prompt).

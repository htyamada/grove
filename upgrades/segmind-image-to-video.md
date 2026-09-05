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

### 1.1 Grove has no data-handling-warning consent UI yet (blocking precondition)

Before wiring the picker to Segmind's video start image, check whether
this still holds: **Grove does not currently collect
`accept_data_handling_warnings` consent for *any* media type.**
`lib/llemon_djview/imagegen.py`'s `_edit_result()` hardcodes
`'accept_data_handling_warnings': False` (see the comment there, "Grove
does not yet collect data-handling-warning consent (Task 13 Phase 2)"),
so a warned transport — Segmind's `qwen-image-edit` upload path included —
is reported as unusable (`_edit_input_usability()`'s `"requires accepting
a data-handling warning"` branch, `imagegen.py` around line 195-210) and
is unreachable through Grove today, for images, even though LLemon's
backend has supported it since Task 8 Phase 5.

This is an untracked prerequisite, not something specific to video: no
Grove media type has the UI element LLemon's
`~/src/hty7/python3/prj/llemon/specs/mediagen-image-spec.md`,
"Data-handling warnings and caller acceptance" describes (a persistent
checkbox, default unchecked, anchored to the affected control, distinct
from the transient `#model-info-notices` area, that only passes
`accept_data_handling_warnings=True` when explicitly checked at
submission time). Building it once, generically, would unblock both
Segmind image editing's `qwen-image-edit`/etc. upload paths *and* this
video upgrade — consider doing it there rather than building a
video-only one-off. There is no existing "Task 13 Phase 2" tracking file
in `upgrades/`; this may be worth its own file rather than folding it
into this one.

#### Planned implementation (drafted 2026-09-04, revised after review — not yet built)

The contract requires a warned model to stay *selectable* (the user must
pick it to see its warning and consent) while only the *edit action* is
gated on consent. `_operation_state()` already has the vocabulary for
this split — it returns `(eligible, enabled, reason)`, and `detail ==
'summary'` already does `eligible=True, enabled=False` for an analogous
reason.

A first draft of this plan conflated two different questions that need to
stay separate: "could this model ever need consent" (a static, schema-level
fact, useful only for annotating the model dropdown) versus "does *this*
submission need consent" (a function of which specific images/roles are
actually assigned, which is the only thing that may gate the request). A
review caught four places the draft got this wrong; the design below
folds in the fixes.

**Two-layer warning determination, client and server alike:**
1. *Schema-level* (`editOptionState()` in JS, `_operation_state()` in
   Python) — unchanged in spirit: still used for model-list annotation
   and for catching a genuinely broken schema (`unavailable_transport`/
   `unsupported`). Its `eligible`/`disabled` half **must stop being forced
   false for the merely-warned case**, so the model stays selectable —
   that's the eligible/enabled split described above. Its `enabled` half
   *keeps* defaulting to `False` for a warned candidate unless the caller
   explicitly passes `accept_data_handling_warnings=True`; that default is
   intentional (it's what makes the flag meaningful at all) and is not
   itself the bug. What it must **not** be used for is deciding whether
   *this* request needs consent — its per-role AND/OR aggregation is
   schema-wide, not assignment-aware (see point 2 below), so a caller that
   wants only "is this schema fundamentally broken, ignoring consent"
   (`_do_edit_image()`'s pre-check, below) has to pass
   `accept_data_handling_warnings=True` itself to suppress the
   warned-lowers-`enabled` default and isolate genuine unusability; the
   actual per-request consent gate is entirely the assignment-level check
   in point 2.
2. *Assignment-level* (new: a JS function and a Python function, kept in
   parallel) — looks at the roles actually in use for the current
   request (client: `editImageRoles`; server: each `canonical_images[i]
   ['role']` from `normalize_edit_inputs()`) and asks, only for those
   roles, whether any resolves to a warned transport. This is what
   actually shows/hides the consent checkbox, what text it shows, and
   what gates submission — both client-side (UX) and server-side
   (Grove's own pre-flight 400, independent of LLemon's own real
   enforcement per the spec's "cannot be bypassed by any frontend"
   clause).

- **`lib/llemon_djview/imagegen.py`**:
  - `_operation_state()` gains a keyword-only `accept_data_handling_warnings:
    bool = False`. Both warned-return sites (the per-role branch around
    line 195-196, the single-scope branch around line 209-210) change from
    `(False, False, reason)` to `(True, False, reason)`, or `(True, True,
    None)` when the flag is `True`. This also fixes `_select_model()`
    (which reads `_operation_state(...)[0]`) so a warned model can become
    the default/selected model instead of being skipped entirely.
  - New `_resolved_edit_warning_reason(edit_inputs, images)` — given the
    schema and the actual `canonical_images` (each optionally carrying a
    `role`), reuses `_source_kind_usability()` (line 100) per resolved
    scope (`edit_inputs['roles']` entry matched by `image['role']` for a
    named schema, otherwise `edit_inputs` itself) and returns the first
    verbatim `transport_warnings[...]` message whose transport is
    actually exercised, or `None`. This is the **only** thing that decides
    whether *this* request needs consent — not `_operation_state()`'s
    aggregate.
  - `_do_edit_image()`:
    - calls `_operation_state(selected_row, 'edit_images',
      source_kind='data_url', accept_data_handling_warnings=True)` (flag
      forced `True`) purely to isolate genuine unusability from the
      warned-consent question — a schema-level warned aggregate must
      never 400 a request whose actual roles don't need consent (review
      point 2).
    - parses acceptance strictly: `accept_raw = data.get(
      'accept_data_handling_warnings')`; if present and not literally a
      JSON boolean, 400 with an explicit error; consent is
      `accept_raw is True` — `"false"`, `"true"`, `1`, or any other
      truthy JSON value must **not** count as acceptance (review point 4;
      `bool(...)` coercion was the bug).
    - right after `canonical_images = normalize_edit_inputs(...)`
      succeeds (before touching the filesystem, matching this function's
      existing fail-fast ordering), calls
      `_resolved_edit_warning_reason(edit_inputs_schema, canonical_images)`;
      if it returns a message and consent wasn't given, 400 "requires
      accepting a data-handling warning".
    - threads the validated boolean through to `_edit_result()`/
      `_edit_stream()`, replacing the hardcoded `False` at line 1792, for
      both the streaming and non-streaming call paths.
  - The "Grove does not yet collect consent" comments at ~203-210 and
    ~1788-1792 get rewritten to describe the new mechanism.

- **`lib/llemon_djview/templates/llemon_image/image.html`**:
  - `roleDataUrlUnusableReason()` (lines 367-379) **stops treating a
    warned transport as unusable** — it drops the `transport_warnings`
    check entirely and returns non-null only for genuine
    `unavailable_transport`/`unsupported`. This function is also what
    `currentEditImagesMaxCount()` (line 1449) and
    `_editInputsSignatureFor()` (line 1466) use to compute the picker's
    usable-role cap and the compatibility signature — leaving it
    warning-blind fixes both consumers for free (review point 1): a
    warned role now counts toward the picker's capacity instead of
    shrinking it, and switching to a model whose only difference is a
    role's warning text no longer wrongly clears the user's current
    image/role assignment (a *genuine* usability change still does,
    since that part of the function is untouched).
  - `editOptionState()` (lines 381-448) keeps its per-role AND/OR
    eligibility check (unaffected — still needed to catch a truly broken
    named schema) but **no longer returns `disabled: true` for the
    merely-warned case**; for the ordered/single-scope branch it still
    returns `warned`/`messages` (deterministic there, no per-assignment
    variability). For the named-schema branch, `warned`/`messages` are
    no longer computed here at all — see the new function below.
  - New `resolvedEditWarning(option)`: given the currently selected
    model row, mirrors `_resolved_edit_warning_reason()` — for a named
    schema, considers only the roles actually in play (every `required`
    role, plus any `optional` role present in the current
    `editImageRoles` assignment), classifies each with the existing
    `sourceKindUsability()`, and returns `{warned, messages}` from
    whichever of those are both usable and warned; for an ordered/
    single-scope model it delegates to `editOptionState()`'s
    `warned`/`messages`. This is what actually reflects "does *this*
    submission need consent" (review point 2) — an optional role that
    isn't currently assigned contributes nothing, and a required warned
    role always contributes regardless of assignment.
  - New markup as the last `.row` inside `edit-opts` (after
    `edit-size-note`, before `edit-opts`'s closing `</div>` at line 194):
    a hidden-by-default `#edit-consent-row` holding a message element
    (`#edit-consent-message`, styled like `#model-info-notices.warning`'s
    `#a60`) and a checkbox, following the existing label-wraps-checkbox
    convention (`#up-enhance`, line 154).
  - New `refreshEditWarningState()` (no arguments — always reads current
    global state: the selected `edit-model-sel` option plus current
    `editImageRoles`/`editImageFnames`): computes `disabled`/`reason` from
    `editOptionState()` and `warned`/`messages` from `resolvedEditWarning()`;
    shows/hides `#edit-consent-row` and sets its message text verbatim;
    resets the checkbox to unchecked only when the `(provider, api, model
    id, messages)` key actually changes from last time (not on every
    call) — model ids are only unique within a provider/API, so a plain
    `(model id, messages)` key would wrongly preserve a checked box across
    a provider switch that happens to reuse the same model id and warning
    text; `currentProvider` and `CREATOR_PRESENTATION.api` are both
    already in scope here. Sets
    `edit.availability = {enabled: !!option && !disabled && (!warned ||
    checkbox.checked), ...}`; calls `updateActionAvailability()`.
  - Call sites for `refreshEditWarningState()` (review point 3 — the
    original draft would have called a stateful consent helper once per
    dropdown option while building the model list, resetting consent
    repeatedly and leaving it associated with whichever model happened to
    be rendered last):
    - End of `_applyEditMetadata()`, **after** its existing loop finishes
      populating every `<option>` — covers initial page load and a
      provider switch, both of which already call this function.
    - The `edit-model-sel` `change` handler (replacing its current ad hoc
      `edit.availability` computation at lines ~762-766).
    - End of `renderEditImagesList()` — the single re-render function
      already called after every image add/remove/move/clear/
      compatibility-reset, so hooking it there covers all of those
      mutations in one place.
    - The per-thumbnail role `<select>`'s `change` handler (line 1552),
      which mutates `editImageRoles` directly without going through
      `renderEditImagesList()`.
    - `#edit-consent-checkbox`'s own `change` listener (so checking the
      box without touching the model or images re-enables the button).
  - `handleEditSubmit()` always sends `accept_data_handling_warnings:
    !!checkbox.checked` (harmless when unwarned, per the LLemon spec's
    "unaffected request" clause) and defensively refuses to submit if the
    row is visible and unchecked, mirroring this function's existing
    defensive re-checks.

- **Tests**:
  - `tests/test_image_creator_render.py`'s three warned-state assertions
    (`(False, False, 'requires accepting a data-handling warning')`, at
    roughly lines 282, 409, 441) become `(True, False, reason)`, each
    gaining a sibling case with `accept_data_handling_warnings=True`
    asserting `(True, True, None)`.
  - New Python tests for `_resolved_edit_warning_reason()`: a required-role
    warned schema returns the message regardless of which image is which;
    an all-optional schema (`warned`/`clean` roles, mirroring the JS
    fixture below) returns `None` when the image is assigned to `clean`
    and the verbatim message when assigned to `warned` — this is the
    direct regression test for review point 2 on the server side.
  - New Python test(s) for `_do_edit_image()`'s strict boolean parsing
    (review point 4): `"true"`, `"false"`, `1`, and `null` for
    `accept_data_handling_warnings` must not be treated as acceptance
    (only JSON `true` may 400-pass the gate); a non-boolean value 400s
    with its own error rather than silently coercing.
  - New Python test(s) exercising `_do_edit_image()` end-to-end for a
    named, required-warned schema through **both** the non-streaming
    path (direct `_edit_result` return) and the streaming path
    (`_edit_stream`'s generator) — asserting both reject without
    consent and both forward `accept_data_handling_warnings=True` to the
    backend when consent is given (extends
    `EditResultBackendForwardingTests`, which already calls
    `view._edit_result(...)` directly and will need the new parameter
    threaded through its existing calls).
  - `tests/js/edit_images_dom_test.js` (the existing jsdom harness for
    this exact UI, run via `tests/test_llemon_image_edit_dom.py`) already
    has scenarios this change directly invalidates and must update rather
    than leave passing-for-the-wrong-reason:
    - "role dropdown disables unusable options with an explanatory label"
      (~line 367): the `warned` role option must **no longer** be
      `disabled` (only `unreachable` still should be); it may keep an
      informational, non-blocking annotation.
    - "selection cap is reduced to the usable-role count, not the
      declared max" (~line 383): with `warned` now usable, the cap for
      `mixed-optional-roles` becomes 2 (`warned` + `clean`), not 1 —
      update the fixture expectations accordingly.
    - "switching between same-shaped schemas with different role facts
      clears a stale selection" (~line 402, `compat-a`/`compat-b`): since
      a warning-only difference no longer makes a role unusable, this
      scenario's premise inverts — assert the selection is **preserved**
      across a warning-only change, and add a separate case (a genuine
      usability change, e.g. `available_backend_transports` losing the
      required transport) that still clears it, so the "still clears on
      real incompatibility" guarantee keeps a regression test.
    - New steps: assigning an image to `mixed-optional-roles`' `clean`
      role shows no consent row; reassigning the same image to `warned`
      shows the row with the exact fixture message
      (`'uploads leave LLemon-managed storage'`) and disables the submit
      button until checked; checking the box enables submission without
      reselecting the model; switching models resets the checkbox;
      switching from a warned model to an unwarned one hides the row and
      re-enables the button; the initial page load and a provider switch
      both reflect the correct consent state without any user
      interaction first (review point 3).
    - New step for the provider-scoped reset key: check the consent box
      for a warned model, then switch to a
      *different provider* whose model list happens to reuse the same
      model id and the same warning text (a new fixture pairing needed
      alongside `mixed-optional-roles`/`compat-a`/`compat-b`) — the
      checkbox must come back unchecked and the submit button disabled,
      not silently carry the prior provider's acceptance over.

- **Docs**: `specs/mediagen-image-user-interface-impl.md`'s
  "Warning-consent asymmetry" section (lines 349-360) gets rewritten to
  describe the implemented mechanism instead of the current gap; this
  note above gets marked resolved once done; a new
  `upgrades/data-handling-warning-consent.md` (house style matching this
  file) becomes the tracking file this note says doesn't exist yet.

- **Out of scope for that piece of work**: actually wiring Segmind's video
  start-image picker (this file's §1.2, untouched by it) and any warned
  transport outside image editing.

### 1.2 Swap the manual URL field for the picker

The hty7-dependent semantics this needs — exactly when the availability
gate opens, when `accept_data_handling_warnings` may be set, and what the
new upload-specific error types mean — are now designed in
`specs/mediagen-video-user-interface-spec.md`, "Segmind image-to-video
integration", "Design: the hty7-dependent contract". What follows here is
the mechanical "where in the code" this touches, not that contract.

Once 1.1 is unblocked (or if a video-only consent checkbox is built
instead), the mechanical picker swap in `video.html` is small — Venice's
own start-image field already does exactly this:

- Current segmind field: `video.html` line 274-275
  (`#segmind_start_image_url`, a plain `<input type="url">`), shown/hidden
  by `showSegmindStartImage` (line 866-867, 882, 893).
- Current segmind submit: `video.html` line 1482-1485 reads that input's
  value directly into `body.image_url`.
- What to mirror instead: Venice's start image already uses the shared
  picker (`#image-picker`, `pickerTarget`) and the shared
  `startImageUrl`/`startImageFname` JS state (declared line 381, set by
  the picker at line 1290, rendered at line 429-430, submitted at line
  1412 as `if (startImageUrl) body.image_url = startImageUrl;`). Making
  Segmind's start-image control a `pickerTarget === 'start'` consumer of
  that same state, instead of its own separate input, is the actual
  "let the picker be used" change this file is named for.
- Server side, `lib/llemon_djview/videogen.py` line 691-697: the
  `provider == 'segmind'` branch already converts whatever it's given
  through `self._data_reference_for_api(request, clean_value)` (line
  883-906) — a private Grove media URL becomes a `data:` URL, a public URL
  passes through unchanged. This part needs no change; it already produces
  exactly the `data:` value the new transport accepts. What's missing is
  passing `accept_data_handling_warnings=True` into `generate_kwargs` when
  (a) the resolved value is a `data:` URL and (b) the consent checkbox
  from 1.1 was checked — mirror whatever shape Task 13 Phase 2 settles on
  for images (`imagegen.py` line 1784-1792 is the equivalent dispatch
  site there).
- Gate the picker's availability, per model, on
  `model_presentation(model, 'segmind').available_backend_transports`
  containing `'provider_upload'` (see "Status" above) — not on the model
  simply being `wan-2.2-i2v-fast`, so a future model added to the same
  gate on the LLemon side doesn't need a matching Grove hardcode.
- Open UI question, not yet decided: does the manual public-URL field stay
  as a fallback alongside the picker (for a genuinely public,
  already-hosted image), or does the picker fully replace it? Venice's
  field is picker-only; Segmind's original field was manual-only because
  the picker path didn't exist. Decide this when implementing.

## Explicitly out of scope for this file

Any hty7/LLemon-side change (done, see "Status"); any Segmind
image-to-video model other than `wan-2.2-i2v-fast` (not live-validated);
investigating the aspect-ratio shape variance noted above; a general
cross-media-type redesign of Grove's data-handling-warning UI beyond
what's needed to unblock 1.1 (though building it generically rather than
video-only is recommended, per 1.1's own note).

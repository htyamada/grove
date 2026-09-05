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

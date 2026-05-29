# Video Generation User Interface Specification

## Implementation Status

**Implemented.**

### Note on Unified Gallery

The video and image galleries, archives, uploads, and category systems are unified. See [mediagen-unified-gallery-spec.md](mediagen-unified-gallery-spec.md) for the complete gallery and archive specification. This document focuses on video-generation-specific features (creator, video-to-video mode inference, reference images).

| File | Role |
|------|------|
| `lib/llemon_djview/videogen.py` | Django view set for video generation, gallery, uploads, archive, notes/tags |
| `lib/llemon_djview/templates/llemon_video/` | Templates for video index and Video Creator |
| `lib/llemon_djview/templates/llemon_image/` | Shared Media gallery/archive/uploads templates |
| `hty7/llemon/mediagen/__init__.py` + `hty7/llemon/mediagen/videogen/__init__.py` | Shared mediagen config loading plus video-generation config accessors for `media_dir`, `notes_dir`, tags, and notes slot |
| `hty7/llemon/core/notes_db.py` | Package-neutral SQLite notes/tag store |

## Overview

The video-generation Django UI is integrated with the unified media gallery system (see [mediagen-unified-gallery-spec.md](mediagen-unified-gallery-spec.md)). Both image and video galleries share the same storage:

| Area | Directory | Shared |
|------|-----------|--------|
| Gallery | `{LLEMON_GALLERY_DIR}` (configurable) or `{media_dir}/gallery` | Yes |
| Archive | `{LLEMON_ARCHIVE_DIR}` (configurable) or `{media_dir}/archive` | Yes |
| Uploads | `{LLEMON_UPLOADS_DIR}` (configurable) or `{media_dir}/uploads` | Yes |
| Categories | `gallery/db/gallery.db` | Yes |
| Notes DB | `notes_dir/notes.db` | Yes (with image generation) |

Uploads are shared by the Media app and can display both image and video files.
Video Creator uses image files from uploads, gallery, or archive as start
images, end images, and reference images for video models.

## Routes

The deployed Django front ends include `llemon_djview.urls` at `/llemon/`.
That shared URL module instantiates `LLemonMediaViewSet('llemon_image',
'llemon', base_nav=..., nav=...)` and exposes Video Creator as the
`video_creator` page inside the combined Media app. `LLemonVideoGenViewSet`
remains available for direct reuse, but host projects should not carry local
view/URL wrappers for the deployed LLemon UI.

| Parameter | Purpose |
|-----------|---------|
| `base_nav` | Left-side navbar items (list of `{'name': str, 'url': str}` dicts) |
| `nav` | Right-side navbar items prepended before the section-specific links on every page; optional |

The section-specific right-side links are Image Creator, Video Creator,
Gallery, Uploads, and Archive. They are appended after any items supplied via
`nav`.

| URL name | Method | Purpose |
|----------|--------|---------|
| `media` | GET | Media app index |
| `video_creator` | GET | Video Creator form |
| `gallery` | GET | Shared Media gallery |
| `archive` | GET | Shared Media archive |
| `uploads` | GET | Shared Media uploads |
| `video_generate` | POST | Submit a video generation request |
| `video_model_note` | GET/POST JSON | Read/write model notes and tag state |
| `video_models_json` | GET JSON | Refresh provider model list |

Shared file, thumbnail, upload, delete, and archive/move operations use the
canonical Media URL names (`image_file`, `thumbnail`, `uploads_image_file`,
`archive_image_file`, `delete_image`, `move_to_archive`, etc.) because those
operations are type-aware.

## Creator

The creator page follows the image-generation page structure: a left result preview and
form area, a narrow model tag filter column, and a right sidebar for model notes
and uploaded source/reference image selection. It selects provider, model,
duration, prompt, and Venice-specific fields. Model notes and tri-state tags
follow the image-generation interaction model: textarea blur saves notes, tag clicks
save immediately, and the special `block` reverse-tag is used by model
filtering.

When a video generation request completes, the creator displays the returned
video immediately and renders the same metadata summary that is written to the
video sidecar. The summary includes provider, API, model display name,
duration, selected generation options, saved filename, and prompt when those
values are present. When the creator is opened from a gallery Reload action,
the result preview is populated from query parameters derived from the sidecar
and uses the same metadata rendering path.

Neither `resolution` nor `aspect_ratio` is sent by default. The UI and view
only include them when the user explicitly fills the field. The `audio` field
is handled the same way: the default selector value is omitted, while explicit
Yes/No choices are sent. Some Venice video models reject requests that include
default-valued optional parameters, so omitting them is the safe default.

The image picker (start, end, reference, and generic image buttons) provides
two tabs: **Uploads** and **Source Dirs**. The Uploads tab lists files from the
uploads directory. The Source Dirs tab is shown when source directories are
configured (via `source_dirs` in `llemon.conf`) and provides an in-picker
navigable browser backed by the `/media/source-dirs/json/` API. See
[mediagen-unified-gallery-spec.md](mediagen-unified-gallery-spec.md) §Source
Directories for configuration.

For requests that include media, djview must not send its private HTTP(S) media
URLs to the provider. Uploaded/gallery/archive/source-dir media selected in the
UI is converted server-side to `data:<mime>;base64,...` before calling the
backend. This applies to all providers and to all data-bearing fields: start
images, end images, reference images, scene images, audio inputs, and video
inputs. Public external URLs may pass through unchanged. Converted data URLs are
request payloads only: the video sidecar stores the original selected URL/path
for media inputs so gallery Reload can replay the selection without persisting
any `data:` URL longer than 30 characters.

For Venice models, the creator infers the mode from recognized model-id
suffixes:

| Mode | Suffixes | UI media inputs |
|------|----------|-----------------|
| Text to Video | `-text-to-video`, `-text-to-video-private` | none |
| Image to Video | `-image-to-video`, `-image-to-video-private` | start image, end image |
| Reference to Video | `-reference-to-video`, `-reference-to-video-private` | ordered reference images |
| Video to Video | `-video-to-video`, `-video-to-video-private` | (no controls; video input not supported) |
| Transition | `-transition`, `-transition-private` | start image, end image |
| Other | any other suffix | start image, end image, ordered reference images |

Video-to-video mode hides the start, end, and reference buttons; video input
is not currently supported for those models.

Within Venice reference-to-video, the creator distinguishes two assumed
families. This is an implementation assumption rather than a provider-guaranteed
contract, because Venice does not currently expose a better machine-readable
way for LLemon to separate Kling-style and Grok-style reference-video models:

| Family | Matching model IDs | Creator inputs |
|--------|--------------------|----------------|
| Kling-family R2V | starts with `kling-` and uses the reference-to-video suffixes | `Elements` plus optional `Scene Images` |
| Grok-family R2V | starts with `grok-` and uses the reference-to-video suffixes | flat ordered `Reference` images |

For Kling O3 R2V, the `Elements` selection is displayed and labeled as
`@Element1`, `@Element2`, ... and the optional scene-image selection is
displayed and labeled as `@Image1`, `@Image2`, ... to match Venice's prompt
conventions. The POST body still uses package-normalized fields:
`reference_image_urls` for elements and `scene_image_urls` for scene images.
The Venice backend translates those to the provider request shape.

For Grok Imagine R2V, the creator shows only the flat ordered reference-image
selector and hides Venice audio controls because that model family does not
support audio generation.

For OpenRouter models, image-input buttons are shown only when the selected
model metadata reports the corresponding supported parameter. If
`supported_frame_images` is present, it decides whether the creator shows the
start-image button, the end-image button, or both. Otherwise `frame_images`
falls back to enabling both exact-frame controls. `input_references` enables
the reference-image button. The creator also provides a separate generic
multi-image selector for OpenRouter's `images` field; when no images are
selected, that field is omitted from the POST body.

The creator also reads OpenRouter's `/videos/models` metadata. If the selected
model reports supported aspect ratios or the passthrough option `aspectRatio`,
the aspect-ratio selector is shown. If the selected model supports audio output
or normalized `generate_audio`, the audio selector is shown. If the model allows
`negativePrompt`, the negative-prompt input is shown. If it allows
`enhancePrompt`, an enhance-prompt checkbox is shown. Any remaining
`allowed_passthrough_parameters` that the UI does not understand are displayed
as “Unknown provider options”.

OpenRouter fields are sent only when the user actually selects or enables them;
no image field is included in the POST body otherwise. Provider validation
remains authoritative for model-specific requirements.

Start and end selections are displayed as compact thumbnails labeled `Start` and
`End`, rather than as filenames. Reference selections are displayed as compact
thumbnails labeled `@Image1`, `@Image2`, etc.; those labels match the submitted
`reference_image_urls` order used by Venice prompt references.

## Gallery and Archive

Gallery and archive are part of the unified media gallery system (see [mediagen-unified-gallery-spec.md](mediagen-unified-gallery-spec.md) for complete specification).

Video-specific details:

- Thumbnails are created lazily with `ffmpeg` (frame at 1s, retrying at 0.1s and 0s on failure, scaled to 320px wide)
- When opening a video in the detail overlay, a video player is displayed instead of an image viewer
- Generation saves video files to gallery and writes a JSON sidecar beside the primary file
- The sidecar records provider, API, `model_id`, `model`, `model_display`, duration, prompt, creation time, options, saved file names, and best-effort request/job identifiers
- Data URLs in metadata are truncated to 30 characters in display; options must not contain any `data:` URL longer than 30 characters
- Generation responses return the sidecar object as `meta` plus a label/value `summary` for immediate creator display
- The detail overlay includes a Generate button (when creator URL is available) that opens the video creator with parameters from the sidecar

## Uploads

Uploads are part of the unified media system (see [mediagen-unified-gallery-spec.md](mediagen-unified-gallery-spec.md)). Only image files (`.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`) are accepted. Creator-page image choices use local media URLs; the server converts those local references into data URLs before sending them to provider APIs.

## Notes and Tags

Video generation uses the shared `core.notes_db` schema and reads its database
from `notes_dir`. In the current default `llemon.conf`, image and video
generation share the same `notes_dir`, so they use the same `notes.db` unless
an operator overrides one of the paths.

`notes.json` supports two tag lists. Both are merged across all
`description_dirs` / `extra_dirs` files in first-seen order:

| Field | Semantics |
|-------|-----------|
| `tags` | Standard tristate tags; selecting one in the creator filter shows only models that have it set to true |
| `reverse-tags` | Inverted-filter tags; selecting one *excludes* models that have it set to true; these tags are binary (true/false only, no indeterminate state) |

`notes.json` controls tag visibility/editability in the current UI and
reverse-filter semantics; it does not define tag existence in the notes
database. Removing a tag from `notes.json` hides it from the current UI but
does not delete stored tag state.

`reverse-tags` are processed before `tags` within each file; if a name appears
in both, the reverse definition wins. The combined vocabulary is returned by
`get_tags()`; `get_reverse_tags()` returns only the inverted-semantics subset.
The `block` tag included in the default `notes.json` is a reverse-tag.

Freetext note keys are slot-aware:

| Slot | Key |
|------|-----|
| default / absent | `provider:model` |
| `S` | `provider:model:S` |

Tag keys are always `provider:model`. Unknown stored tags are preserved when the
current `notes.json` vocabulary changes.

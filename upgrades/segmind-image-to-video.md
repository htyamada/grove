# UPGRADE: Segmind Image-to-Video Wiring

Wire Video Creator to LLemon's already-implemented Segmind image-to-video
support (`wan-2.2-i2v-fast`). The gap, required behavior, and the open
source-transport constraint are recorded in
[`specs/mediagen-video-user-interface-spec.md`](../specs/mediagen-video-user-interface-spec.md),
"Segmind image-to-video integration" — that section is authoritative; this
file only tracks the work as pending.

---

## Part 1 — Requirements

### 1.1 Expose the start-image control

Extend Video Creator's client visibility logic (currently Venice/OpenRouter
only) to also show a start-image control when LLemon's normalized
presentation reports `mode: image-to-video` with `allows_start_image: true`
for the selected model, including Segmind's `wan-2.2-i2v-fast`.

### 1.2 Forward the start image as `image_url`

Extend the server side (currently adds `image_url` to generation arguments
only for Venice) to forward the start image as `image_url` for Segmind too.
Segmind does not support an end image — do not add end-image handling for
this provider.

### 1.3 Private-source transport

LLemon accepts only a well-formed public `https://` URL for this model and
rejects `data:` URLs outright. Do not just port Venice's existing
private-media (`data:` URL) conversion path to Segmind — it will fail
LLemon's validation. Before Grove's gallery, archive, or source-directory
picks can be offered for this model, either:

- add a video `provider_upload` transport (paralleling the image-editing
  upload transport), or
- get LLemon to accept direct `data:` input for this model, live-validated
  first.

Until one of those lands, Video Creator must not advertise Segmind
image-to-video as usable with the image picker for private sources.

# UPGRADE: Segmind Image-to-Video Private-Source Transport

Video Creator already wires Segmind's `wan-2.2-i2v-fast` image-to-video
model: it shows a start-image control and forwards the value as `image_url`.
That control is a plain public-URL text field rather than Grove's usual
Gallery/Source-Dirs image picker. This file tracks the one piece still
missing: letting that picker be used for this model's start image.

The gap and constraint are recorded in
[`specs/mediagen-video-user-interface-spec.md`](../specs/mediagen-video-user-interface-spec.md),
"Segmind image-to-video integration" — that section is authoritative; this
file only tracks the work as pending.

---

## Part 1 — Requirements

### 1.1 Private-source transport

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
image-to-video as usable with the image picker for private sources — only
the manual public-URL field.

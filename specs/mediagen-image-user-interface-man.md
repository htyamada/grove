# Imagegen Notes and Tags Configuration

The Django image-generator UI supports free-text per-model notes and
tri-state tags for provider/model pairs. Tag visibility/editability is
configured through `notes.json` files placed in any directory listed in
`description_dirs` or `extra_dirs` under `[*.llemon.mediagen]` in the
effective configuration. In Grove's Django deployment, that effective
configuration is `~/etc/llemon.conf` plus the Grove-local
`etc/llemon_djview.conf` overlay. The active free-text notes slot is
configured with `notes_selector`.

---

## notes.json Format

```json
{
    "//": ["Comment lines are ignored."],
    "tags":  ["block", "tag-one", "tag-two"]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `tags` | list of strings | Visible tag vocabulary shown as tri-state checkboxes in the UI. Tags may be applied to any provider/model pair and are stored independently from free-text notes. |
| `//` | any | Reserved for comments; ignored at load time. |

`notes.json` does not define whether a tag exists in the notes database. It
only controls which stored tag names the current UI can show or edit. Removing
a tag from `notes.json` hides it from the UI but leaves stored tag state intact.

---

## Tags

Tags describe facts about a provider/model pair, not about a specific note
slot. Each tag checkbox has three states:

| State | Stored value | Meaning |
|-------|--------------|---------|
| not tested | absent | No explicit information has been recorded. |
| yes | `true` | The tag applies to this provider/model pair. |
| no | `false` | The tag was tested and does not apply to this provider/model pair. |

The database key for tag state is always `provider:model`, even when a
deployment-specific notes slot is active. This allows multiple front-end
deployments to share model facts while keeping their free-text notes separate.

On the creator page, the model dropdown can be filtered by the active tag
vocabulary. Filter checkboxes start clear, except for the special `block` tag.
When one or more ordinary filter tags are checked, a model remains visible if
each selected tag is either `true` or absent for that model; a model is hidden
only when it has an explicit `false` for one of the selected tags.

The `block` tag uses inverted filter semantics. It is selected by default and
left selected when the filter controls are reset. While selected, models with
`block: true` are hidden; models with `block: false` or no `block` state remain
visible.

The filter controls include a button that clears ordinary selected filter tags
and restores the special `block` filter to its default selected state.

Older database rows that stored tags as a JSON list are interpreted as `yes`
states for each listed tag.

---

## Notes Slot

The slot identifier namespaces the database key used to store free-text
per-model notes. It is configured with `notes_selector` under
`[*.llemon.mediagen]`, not in `notes.json`.

Without a slot, a model's notes are stored under the key `provider:model`.
With a slot value such as `hty7`, notes are stored under
`provider:model:hty7`. The special value `"default"` selects the unslotted
namespace.

This allows different front-end deployments that share a `media_dir` to
maintain independent per-model notes for the same models. For example, a `hty7`
deployment and a `qat` deployment can each have their own notes without
interfering with each other. Tags for the same provider/model pair remain
shared across those deployments.

---

## Multiple Files

When `description_dirs` and `extra_dirs` together resolve to more than one
directory containing a `notes.json`, the files are merged at startup:

- **`tags`**: combined from all files in directory order; duplicates dropped,
  first occurrence kept.

---

## Placement

Place `notes.json` in any directory listed in `description_dirs` or
`extra_dirs` under the effective mediagen configuration section:

```toml
[hty7.llemon.mediagen]
description_dirs = ["~/share/llemon/mediagen"]
extra_dirs        = ["~/etc/llemon/mediagen"]
notes_selector    = "hty7"
```

A `notes.json` in any configured directory can supply visible tag vocabulary.
The deployment's selected free-text note slot comes from `notes_selector`.

---

## Example

Shared visible tag vocabulary (`~/share/llemon/mediagen/notes.json`):

```json
{
    "tags": ["block", "very slow", "very expensive", "good quality"]
}
```

With the example configuration in scope, the UI shows the four tags as
tri-state checkboxes. Tag states are stored under `provider:model`; free-text
notes are stored under `provider:model:hty7`.

# Media Creator Presentation and Refresh Specification

## Implementation status

**Implemented.**

This specification defines the provider-independent presentation boundary
shared by Grove's image and video creators. It is an enduring frontend
architecture contract, not a provider rollout mechanism. Provider visibility,
provider usability, operation availability, backend defaults, action request
schemas, and validation behavior are owned by the modality specifications.

## Layer and provider boundaries

Image and video provider choices come directly from the corresponding public
`hty7.llemon.mediagen` `PROVIDERS` list. Grove must not maintain a second
provider support list, inspect raw provider catalogs, import a provider backend,
or infer capabilities from provider or model identifiers.

Provider-specific presentation facts cross the public mediagen facade as
normalized metadata or capabilities. A compatibility branch may remain in a
modality view only when no provider-neutral public fact expresses the existing
wire behavior exactly; it must be documented and covered by regression tests.
Provider assumptions must not be relocated into the shared creator layer.

The shared boundary covers presentation and refresh mechanics only. Image
generation, editing, and upscaling remain independent operations, and video
generation retains its own request and result schema. The view sets and backend
interfaces are not merged.

This boundary does not define provider eligibility, upload or asset lifecycles,
multi-image editing, model verification, pricing controls, a progress/event
protocol, or a new input transport. Those features require their own modality
or provider specifications and must not be introduced as presentation-layer
fallbacks.

## Presentation contract

`lib/llemon_djview/media_creator.py` builds the stable outer contract. A creator
presentation contains:

| Field | Meaning |
|------|---------|
| `provider` | Canonical provider identifier |
| `api` | Canonical API identifier |
| `target` | Provider-level cache identity |
| `operations` | Mapping of operation name to operation presentation |

The provider-level `target` contains `provider`, `api`, `operation: "provider"`,
and `model: null`.

Each operation presentation contains:

| Field | Meaning |
|------|---------|
| `operation` | Stable operation identifier |
| `model_options` | Models with identifier, display name, description, and already-fetched normalized metadata where applicable |
| `selected_model` | Model currently selected for presentation; distinct from the backend default |
| `default_model` | Backend default model, or `null` when none exists |
| `defaults` | Modality-specific control defaults |
| `controls` | Modality-specific provider/operation controls |
| `availability` | Existing enabled state and optional reason |
| `selected_target` | Selected-model presentation target, or `null` for a model-less operation |
| `notes` | Notes/tag lookup identity and model tag state |

A selected-model target has a complete identity containing `provider`, `api`,
`operation`, and `model`, plus its target-scoped `controls`. Complete identity
is required because the same provider/model may have different APIs or
operation controls.

Image generation and editing use separate operation records. Image upscaling
is model-less. Video generation has its own operation record and
modality-specific controls. Contract builders deep-copy mutable inputs so a
caller cannot change a built presentation by retaining and mutating an input
container.

The initial creator render and provider-refresh JSON use this same
authoritative contract. Flat Django context values may remain where the server
needs them to render HTML, but refresh JSON must not duplicate the contract and
the browser must not reconstruct a second authoritative model/capability map.

## Refresh and cache lifecycle

Provider and model changes use the shared browser target controller in
`templates/llemon_media/media_creator.js`.

- Cache keys include provider, API, operation, and model.
- An uncached provider selection retrieves one provider presentation.
- A model selection retrieves or selects only the chosen model target.
- A response is applied only when it belongs to the newest selection sequence;
  delayed older responses are discarded.
- Returning to a cached target reapplies its presentation without loading it
  again.
- Existing modality-specific provider failure semantics are preserved: image
  selection rolls back after a failed load, while video selection commits when
  loading begins.

Image provider refresh performs model discovery once and retrieves capability
metadata only for the selected generation model. A subsequent image model-only
target request performs no discovery and retrieves capability metadata only for
that target. Video provider refresh performs one metadata-list operation; model
changes reuse the normalized capability rows already in that presentation and
perform no additional model lookup.

When a browser selection changes, the operation's `selected_model` and
`selected_target` are updated together. Server action handling continues to use
the model in the action request and uses that same model for applicable public
metadata and validation calls. Presentation selection does not introduce a
model requirement for an action whose established contract omits one.

## Preserved behavior and compatibility rules

The presentation boundary does not change provider lists, fallbacks, defaults,
missing-field behavior, operation availability, model filtering, notes/tags,
request omission, streaming, storage, sidecars, EXIF metadata, gallery/source
media flow, or normalized error behavior.

The current provider-coupling decisions are:

| Construct | Resolution |
|----------|------------|
| Venice-specific image-edit sizing message | Retained as a documented image compatibility rule until the public image interface supplies the exact unavailable reason |
| Image backend construction | Retained as modality dispatch through the public imagegen facade |
| Video import of Venice constants and predicates | Removed; Grove consumes public `videogen.model_presentation()` facts |
| Video model suffix/prefix and `upscale` tests | Removed from Grove; normalized presentation supplies mode, reference family, media-input booleans, and upscale state |
| Venice/OpenRouter video request mapping | Retained as documented provider wire compatibility until the public video request interface can express it without changing omission behavior |

Catalog metadata may describe facts but must not independently expand model or
operation availability.

## Regression and performance requirements

Automated tests must cover initial rendering, provider and model selection,
cache reuse, out-of-order responses, model filtering, descriptions, notes/tag
state, model-dependent controls, omission of unselected optional parameters,
action request construction, normalized failures, output saving, metadata, and
source-media redaction. Tests use injected catalog/capability data and must not
make live provider calls.

The pre-change image path performed one catalog lookup and one capability
lookup per listed model on initial render or provider switch; a model switch
made no request because every capability record was prefetched. The permanent
performance bounds are stricter:

- image initial/provider refresh: one discovery plus one selected-model
  capability lookup;
- image model-only refresh: no discovery plus one selected-target capability
  lookup;
- video initial/provider refresh: one metadata-list operation; and
- video model selection: no additional lookup.

`tests/test_media_creator_javascript.py` pins cache identity, cache reuse,
out-of-order response handling, and modality failure semantics. Render/view
tests pin the authoritative payload and lookup counts. Changes to the public
mediagen presentation boundary also require the focused LLemon media tests,
the full LLemon suite and lint, the full Grove suite, and the deployed Django
system check. Automated acceptance makes no live provider calls. A manual smoke
pass over affected, already-usable provider paths requires explicit
authorization and generated or otherwise non-sensitive inputs.

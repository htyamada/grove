"""Node-based tests for shared media creator browser refresh mechanics."""

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = (
    ROOT / 'lib' / 'llemon_djview' / 'templates' /
    'llemon_media' / 'media_creator.js'
)


class MediaCreatorJavascriptTests(unittest.TestCase):
    def _run_node(self, body: str) -> None:
        source = CONTROLLER.read_text(encoding='utf-8')
        result = subprocess.run(
            ['node', '-e', source + '\n' + body],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_out_of_order_provider_response_is_ignored(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
let resolveSlow;
let resolveFast;
const applied = [];
const controller = createMediaRefreshController({
  initialTarget: 'initial',
  initialData: {provider: 'initial'},
  load: provider => new Promise(resolve => {
    if (provider === 'slow') resolveSlow = resolve;
    else resolveFast = resolve;
  }),
  apply: (data, provider) => applied.push(provider),
});
(async function () {
  const slow = controller.select('slow');
  const fast = controller.select('fast');
  resolveFast({provider: 'fast'});
  assert.equal((await fast).applied, true);
  resolveSlow({provider: 'slow'});
  assert.equal((await slow).stale, true);
  assert.deepEqual(applied, ['fast']);
  assert.equal(controller.current(), 'fast');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")

    def test_cached_provider_is_applied_without_loading(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
let loads = 0;
const applied = [];
const controller = createMediaRefreshController({
  initialTarget: 'initial',
  initialData: {provider: 'initial'},
  load: async provider => { loads += 1; return {provider}; },
  apply: (data, provider) => applied.push(provider),
});
(async function () {
  await controller.select('next');
  await controller.select('initial');
  await controller.select('next');
  assert.equal(loads, 1);
  assert.deepEqual(applied, ['next', 'initial', 'next']);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")

    def test_transient_envelope_is_not_cached_replayed_or_applied_when_stale(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
let resolveSlow;
const transients = [];
const applied = [];
const controller = createMediaRefreshController({
  initialTarget: 'initial',
  initialData: {provider: 'initial'},
  load: target => target === 'slow'
    ? new Promise(resolve => { resolveSlow = resolve; })
    : Promise.resolve({presentation: {provider: target}, notices: [target]}),
  value: envelope => envelope.presentation,
  transient: envelope => transients.push(...envelope.notices),
  apply: data => applied.push(data.provider),
});
(async function () {
  await controller.select('next');
  await controller.select('initial');
  await controller.select('next');
  assert.deepEqual(transients, ['next']);
  assert.deepEqual(controller.cache.next, {provider: 'next'});
  const slow = controller.select('slow');
  await controller.select('latest');
  resolveSlow({presentation: {provider: 'slow'}, notices: ['slow']});
  assert.equal((await slow).stale, true);
  assert.deepEqual(transients, ['next', 'latest']);
  assert.deepEqual(applied, ['next', 'initial', 'next', 'latest']);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")

    def test_nested_cached_model_apply_does_not_erase_provider_notice(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
let visibleNotices = [];
const modelTarget = {
  provider: 'next', api: 'images', operation: 'generate', model: 'm1',
};
const modelController = createMediaRefreshController({
  initialTarget: null,
  cache: {},
  load: async () => { throw new Error('seeded target should be cached'); },
  begin: target => {
    if (!target.preserve_notices) visibleNotices = [];
  },
  apply: () => {},
});
const providerController = createMediaRefreshController({
  initialTarget: 'initial',
  initialData: {provider: 'initial'},
  load: async () => ({
    presentation: {provider: 'next', selectedTarget: {value: 'target'}},
    notices: ['catalog warning'],
  }),
  value: envelope => envelope.presentation,
  transient: envelope => { visibleNotices = envelope.notices; },
  apply: presentation => {
    modelController.cache[mediaPresentationTargetKey(modelTarget)] =
      presentation.selectedTarget;
    modelController.select({...modelTarget, preserve_notices: true});
  },
});
(async function () {
  await providerController.select('next');
  assert.deepEqual(visibleNotices, ['catalog warning']);
  await modelController.select({...modelTarget, preserve_notices: false});
  assert.deepEqual(visibleNotices, []);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")

    def test_commit_on_begin_preserves_video_failure_semantics(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
const controller = createMediaRefreshController({
  initialTarget: 'initial',
  initialData: {provider: 'initial'},
  commitOnBegin: true,
  load: async () => { throw new Error('failed'); },
  apply: () => {},
});
(async function () {
  await assert.rejects(controller.select('broken'), /failed/);
  assert.equal(controller.current(), 'broken');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")

    def test_cache_identity_includes_provider_api_operation_and_model(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
const loads = [];
const target = (api, operation, model) => ({
  provider: 'provider-a', api, operation, model,
});
const initial = target('api-a', 'generate', 'model-a');
const controller = createMediaRefreshController({
  initialTarget: initial,
  initialData: {value: 'initial'},
  load: async value => { loads.push(mediaPresentationTargetKey(value)); return value; },
  apply: () => {},
});
(async function () {
  await controller.select(target('api-a', 'edit', 'model-a'));
  await controller.select(target('api-a', 'generate', 'model-b'));
  await controller.select(target('api-b', 'generate', 'model-a'));
  await controller.select(initial);
  assert.equal(loads.length, 3);
  assert.notEqual(loads[0], loads[1]);
  assert.notEqual(loads[1], loads[2]);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")

    def test_generation_control_overlay_distinguishes_empty_sentinel(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
const fallback = {aspect_ratios: ['1:1'], default_aspect_ratio: '1:1'};
assert.equal(mediaOverlayControls(fallback, {}, Object.keys(fallback)), null);
assert.deepEqual(
  mediaOverlayControls(fallback, {aspect_ratios: []}, Object.keys(fallback)),
  {aspect_ratios: [], default_aspect_ratio: '1:1'},
);
assert.deepEqual(
  mediaOverlayControls(fallback, {default_aspect_ratio: null}, Object.keys(fallback)),
  {aspect_ratios: ['1:1'], default_aspect_ratio: null},
);
assert.deepEqual(mediaUnresolvedGenerationState('model-a'), {
  selected_model: 'model-a', selected_target: null,
  availability: {enabled: false, reason: 'model controls could not be loaded'},
});
""")

    def test_generation_choice_modes_keep_open_controls_blank(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
assert.deepEqual(mediaChoiceControlState(['1:1', '16:9'], '16:9'), {
  mode: 'select', choices: ['1:1', '16:9'], value: '16:9',
});
assert.deepEqual(mediaChoiceControlState(['1:1', '16:9'], null), {
  mode: 'select', choices: ['1:1', '16:9'], value: '1:1',
});
assert.deepEqual(mediaChoiceControlState([], 'model-default'), {
  mode: 'open', choices: [], value: '',
});
""")

    def test_generation_choice_dom_application_and_target_ready_sequence(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
function choiceElement() {
  let options = [];
  return {
    style: {display: ''}, value: 'stale',
    get options() { return options; },
    set innerHTML(value) { options = []; this.value = ''; },
    appendChild(option) {
      options.push(option);
      if (option.selected || options.length === 1) this.value = option.value;
    },
  };
}
const elements = {
  aspect_ratio: choiceElement(),
  aspect_ratio_open: {style: {display: 'none'}, value: 'stale-open'},
};
const doc = {
  getElementById: name => elements[name] || null,
  createElement: () => ({value: '', textContent: '', selected: false}),
};

const selected = mediaApplyChoiceControl(
  doc, 'aspect_ratio', ['1:1', '16:9'], null,
);
assert.equal(selected.value, '1:1');
assert.equal(elements.aspect_ratio.value, '1:1');
assert.equal(elements.aspect_ratio_open.value, '');
mediaSetVisibleChoiceControl(doc, 'aspect_ratio', '16:9');
assert.equal(mediaVisibleChoiceControlValue(doc, 'aspect_ratio'), '16:9');

const open = mediaApplyChoiceControl(doc, 'aspect_ratio', [], '16:9');
assert.equal(open.mode, 'open');
assert.equal(elements.aspect_ratio.value, '');
assert.equal(elements.aspect_ratio_open.value, '');
mediaSetVisibleChoiceControl(doc, 'aspect_ratio', '  2:3  ');
assert.equal(mediaVisibleChoiceControlValue(doc, 'aspect_ratio'), '2:3');

let release;
const ready = new Promise(resolve => { release = resolve; });
const applied = mediaAfterTargetReady(ready, () => {
  mediaApplyChoiceControl(doc, 'aspect_ratio', ['4:3'], '4:3');
  mediaSetVisibleChoiceControl(doc, 'aspect_ratio', '4:3');
});
assert.equal(elements.aspect_ratio_open.value, '  2:3  ');
release();
(async function () {
  await applied;
  assert.equal(elements.aspect_ratio.style.display, '');
  assert.equal(elements.aspect_ratio.value, '4:3');
  assert.equal(elements.aspect_ratio_open.value, '');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")

    def test_failed_apply_is_not_cached_and_reselection_retries(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
let loads = 0;
let fail = true;
const controller = createMediaRefreshController({
  initialTarget: 'initial', initialData: {controls: {ok: true}},
  load: async () => { loads += 1; return {controls: {}}; },
  apply: data => {
    if (fail && Object.keys(data.controls).length === 0) throw new Error('unresolved');
  },
});
(async function () {
  await assert.rejects(controller.select('model-a'), /unresolved/);
  assert.equal(Object.hasOwn(controller.cache, 'model-a'), false);
  fail = false;
  await controller.select('model-a');
  assert.equal(loads, 2);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")

    def test_compatible_extra_field_descriptors_can_retain_values(self) -> None:
        self._run_node(r"""
const assert = require('node:assert/strict');
const seed = {name: 'seed', type: 'number', min: -1, max: 100};
assert.equal(mediaSameFieldDescriptor(seed, {...seed}), true);
assert.equal(mediaSameFieldDescriptor(seed, {...seed, max: 200}), false);
assert.equal(mediaSameFieldDescriptor(seed, null), false);
""")


if __name__ == '__main__':
    unittest.main()

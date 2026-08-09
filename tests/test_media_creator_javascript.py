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


if __name__ == '__main__':
    unittest.main()

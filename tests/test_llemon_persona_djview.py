import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
GROVE_LIB = ROOT / 'lib'
HTY7_LIB = Path.home() / 'src' / 'hty7' / 'python3' / 'lib'
for lib in (GROVE_LIB, HTY7_LIB):
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))

try:
    djview = importlib.import_module('llemon_djview')
except Exception as exc:  # pragma: no cover - import environment dependent
    djview = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@unittest.skipIf(djview is None, f'djview import failed: {_IMPORT_ERROR}')
class PersonaDjviewManualSelectionTests(unittest.TestCase):
    def test_macro_route_command_uses_connect_not_start(self) -> None:
        self.assertEqual(djview._macro_route_command('connect'), 'start')
        self.assertIsNone(djview._macro_route_command('/start'))

    def test_load_persona_config_delegates_manual_provider_model_without_service(self) -> None:
        with mock.patch.object(djview, '_persona_load_persona_config', return_value='built') as load_config:
            config = djview._load_persona_config(
                '/tmp/demo.cfg.json',
                None,
                provider='OpenAI',
                model='gpt-test',
                history_path='/tmp/demo.jsonl',
                start_file_path='/tmp/demo.start.md',
            )

        self.assertEqual(config, 'built')
        load_config.assert_called_once_with(
            '/tmp/demo.cfg.json',
            None,
            provider='OpenAI',
            model='gpt-test',
            history_path='/tmp/demo.jsonl',
            start_file_path='/tmp/demo.start.md',
        )

    def test_load_persona_config_uses_service_builder_when_manual_selection_absent(self) -> None:
        with mock.patch.object(djview, '_persona_load_persona_config', return_value='built') as load_config:
            config = djview._load_persona_config(
                '/tmp/demo.cfg.json',
                'svc-demo',
                history_path='/tmp/demo.jsonl',
                start_file_path='/tmp/demo.start.md',
            )

        self.assertEqual(config, 'built')
        load_config.assert_called_once_with(
            '/tmp/demo.cfg.json',
            'svc-demo',
            provider=None,
            model=None,
            history_path='/tmp/demo.jsonl',
            start_file_path='/tmp/demo.start.md',
        )

    def test_load_history_turns_backfills_context_estimate_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'demo.jsonl'
            path.write_text(
                '\n'.join([
                    '{"type":"header","version":1,"metadata":{"model_pricing":{"claude-sonnet-4-5":{"prompt_usd_per_token":"0.000003","completion_usd_per_token":"0.000015","prompt_usd_per_million":"3.000000","completion_usd_per_million":"15.000000","prompt_rate_text":"$3.00/M","completion_rate_text":"$15.00/M"}}}}',
                    '{"id":"t1","model":"claude-sonnet-4-5","user":{"content":"hi"},"assistant":{"content":"hello"},"usage":{"prompt_tokens":150000},"metadata":{}}',
                    '',
                ]),
                encoding='utf-8',
            )

            turns = djview._load_history_turns_from_file(str(path))

        self.assertEqual(
            turns[0]['metadata']['context_estimate']['status_text'],
            'ctx 75% est 150k/200k',
        )
        self.assertEqual(
            turns[0]['metadata']['cost_estimate']['turn_cost_text'],
            '$0.45',
        )


if __name__ == '__main__':
    unittest.main()

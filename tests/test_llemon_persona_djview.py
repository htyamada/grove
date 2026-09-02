import contextlib
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
GROVE_LIB = ROOT / 'lib'
HTY7_LIB = Path.home() / 'src' / 'hty7' / 'python3' / 'lib'
for lib in (GROVE_LIB, HTY7_LIB):
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))

# StreamingHttpResponse.streaming_content iteration reaches Django's own
# internal `from django.conf import settings` (django.http.response), a
# reference this file's mock.patch.object(djview, 'settings', ...) cannot
# reach -- that only replaces the name as seen from llemon_djview's own
# module scope. Configure real Django settings minimally, once, so tests
# that actually drain a streaming response don't depend on some other test
# file in the same process having configured them first as a side effect.
from django.conf import settings as _django_settings
if not _django_settings.configured:
    _django_settings.configure(DEFAULT_CHARSET='utf-8', USE_TZ=True)

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

    def test_chat_resolves_effective_provider_from_the_selected_service(self) -> None:
        """Regression test for a real, pre-existing bug: chat() called
        discover.list_services(config_path), which does not exist -- only
        discover.get_services() does -- so effective_provider/effective_model
        were always empty strings for a service-selected session, silently,
        via the surrounding `except Exception: logger.exception(...)`.
        Fixed by delegating to resolve_effective_provider_and_model(),
        which uses the real discover.get_services()."""
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = types.SimpleNamespace(GET={
            'config': 'demo.cfg.json',
            'service': 'svc-a',
            'history': 'demo_h000001_20260101.jsonl',
        })

        def fake_exists(path: str) -> bool:
            return path == '/configs/demo.cfg.json'

        with (
            mock.patch.object(
                djview, 'render', side_effect=lambda request, template, context: context,
            ),
            mock.patch.object(djview, 'reverse', return_value='/x/'),
            # Real, unconfigured Django settings raise ImproperlyConfigured
            # on any attribute access -- replace the name chat() reads
            # rather than relying on some other test file in the same
            # process having configured them first (which is not a safe
            # assumption to run this file in isolation against).
            mock.patch.object(
                djview, 'settings', types.SimpleNamespace(LLEMON_PERSONA_HOSTS=()),
            ),
            mock.patch.object(
                djview.discover, 'resolve_path', return_value='/configs/demo.cfg.json',
            ),
            mock.patch.object(djview.os.path, 'exists', side_effect=fake_exists),
            mock.patch.object(djview.Config, 'display_name', return_value='Demo Config'),
            mock.patch.object(djview.Config, 'read_type', return_value='chat'),
            mock.patch.object(djview.discover, 'config_id', return_value='demo'),
            mock.patch.object(
                djview.discover, 'get_services',
                return_value=[('svc-a', 'Service A', 'ollama', 'llama3', [])],
            ) as get_services,
            mock.patch.object(
                view, '_load_chat_config',
                return_value={
                    'error': None, 'header_text': '', 'user_tag': 'You',
                    'assistant_tag': 'Assistant', 'e2ee': False, 'tee': False,
                    'edit_responses': False, 'write_history': True,
                    'download_field': None, 'state': None, 'debug': False,
                },
            ),
            mock.patch.object(
                djview.discover, 'resolve_history_path', return_value='/hist/demo.jsonl',
            ),
            mock.patch.object(djview, '_history_macro_name', return_value='demo'),
        ):
            context = view.chat(request)

        get_services.assert_called_once_with('/configs/demo.cfg.json')
        self.assertEqual(context['provider_name'], 'ollama')
        self.assertEqual(context['model_name'], 'llama3')

    def _chat_request(self, *, host: str = '') -> types.SimpleNamespace:
        get = {
            'config': 'demo.cfg.json',
            'service': 'svc-a',
            'history': 'demo_h000001_20260101.jsonl',
        }
        if host:
            get['host'] = host
        return types.SimpleNamespace(GET=get)

    def _chat_mocks(self, *, persona_hosts=(), extra=()):
        def fake_exists(path: str) -> bool:
            return path == '/configs/demo.cfg.json'

        return [
            mock.patch.object(
                djview, 'render', side_effect=lambda request, template, context: context,
            ),
            mock.patch.object(djview, 'reverse', return_value='/x/'),
            mock.patch.object(
                djview, 'settings',
                types.SimpleNamespace(LLEMON_PERSONA_HOSTS=persona_hosts),
            ),
            mock.patch.object(
                djview.discover, 'resolve_path', return_value='/configs/demo.cfg.json',
            ),
            mock.patch.object(djview.os.path, 'exists', side_effect=fake_exists),
            mock.patch.object(djview.Config, 'display_name', return_value='Demo Config'),
            mock.patch.object(djview.Config, 'read_type', return_value='chat'),
            mock.patch.object(djview.discover, 'config_id', return_value='demo'),
            mock.patch.object(
                djview.discover, 'get_services',
                return_value=[('svc-a', 'Service A', 'ollama', 'llama3', [])],
            ),
            mock.patch.object(
                djview.discover, 'resolve_history_path', return_value='/hist/demo.jsonl',
            ),
            mock.patch.object(djview, '_history_macro_name', return_value='demo'),
            *extra,
        ]

    def test_chat_resolves_hosted_url_override_when_host_selected(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._chat_request(host='opah')
        load_chat_config = mock.Mock(return_value={
            'error': None, 'header_text': '', 'user_tag': 'You',
            'assistant_tag': 'Assistant', 'e2ee': False, 'tee': False,
            'edit_responses': False, 'write_history': True,
            'download_field': None, 'state': None, 'debug': False,
        })
        with contextlib.ExitStack() as stack:
            host_url_override = stack.enter_context(mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ))
            stack.enter_context(mock.patch.object(view, '_load_chat_config', load_chat_config))
            for cm in self._chat_mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.chat(request)

        host_url_override.assert_called_once_with('ollama', 'opah')
        load_chat_config.assert_called_once()
        self.assertEqual(load_chat_config.call_args.kwargs['url_override'], 'http://127.0.0.1:15000')
        self.assertTrue(load_chat_config.call_args.kwargs['hosted'])
        self.assertEqual(context['host'], 'opah')

    def test_chat_clears_an_unconfigured_host_silently(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._chat_request(host='not-configured')
        load_chat_config = mock.Mock(return_value={
            'error': None, 'header_text': '', 'user_tag': 'You',
            'assistant_tag': 'Assistant', 'e2ee': False, 'tee': False,
            'edit_responses': False, 'write_history': True,
            'download_field': None, 'state': None, 'debug': False,
        })
        with contextlib.ExitStack() as stack:
            host_url_override = stack.enter_context(mock.patch.object(djview, 'host_url_override'))
            stack.enter_context(mock.patch.object(view, '_load_chat_config', load_chat_config))
            for cm in self._chat_mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.chat(request)

        host_url_override.assert_not_called()
        self.assertIsNone(load_chat_config.call_args.kwargs['url_override'])
        self.assertFalse(load_chat_config.call_args.kwargs['hosted'])
        self.assertEqual(context['host'], '')
        self.assertIsNone(context.get('error'))

    def test_chat_tunnel_failure_shows_error_and_skips_persona_construction(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._chat_request(host='opah')
        load_chat_config = mock.Mock()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                djview, 'host_url_override',
                side_effect=djview.ConfigError(
                    'SSH connection to opah could not be created.',
                ),
            ))
            stack.enter_context(mock.patch.object(view, '_load_chat_config', load_chat_config))
            for cm in self._chat_mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.chat(request)

        load_chat_config.assert_not_called()
        self.assertEqual(context['error'], 'SSH connection to opah could not be created.')

    def _stream_request(self, **overrides) -> types.SimpleNamespace:
        body = {
            'config': 'demo.cfg.json',
            'history': 'demo.jsonl',
            'message': 'hi',
            'service': 'svc-a',
            'provider': 'ollama',
            'model': 'llama3',
        }
        body.update(overrides)
        return types.SimpleNamespace(body=json.dumps(body).encode())

    def test_stream_rejects_a_forged_host_with_zero_tunnel_calls(self) -> None:
        """The actual trust boundary: never trust the page render that
        produced `host`. A host/provider combination that doesn't pass
        validate_host_selection() is rejected outright -- zero tunnel
        calls -- rather than cleared silently the way a stale GET request
        would be."""
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._stream_request(host='not-configured')
        with (
            mock.patch.object(
                djview, 'settings',
                types.SimpleNamespace(LLEMON_PERSONA_HOSTS=('opah',)),
            ),
            mock.patch.object(
                djview.discover, 'resolve_path', return_value='/configs/demo.cfg.json',
            ),
            mock.patch.object(djview.os.path, 'exists', return_value=True),
            mock.patch.object(
                djview.discover, 'resolve_history_path', return_value='/hist/demo.jsonl',
            ),
            mock.patch.object(djview, 'host_url_override') as host_url_override,
        ):
            response = view._stream(request)

        self.assertEqual(response.status_code, 400)
        host_url_override.assert_not_called()

    def test_stream_resolves_hosted_url_override_and_marks_persona_hosted(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._stream_request(host='opah')

        fake_persona = mock.Mock()
        fake_persona.history_path = None
        fake_persona.iterate_history.return_value = iter([])
        fake_persona.stream.return_value = iter([{'content': 'ok', 'done': True}])
        fake_persona.is_e2ee.return_value = False
        fake_persona.is_tee.return_value = False
        fake_persona.has_structured_output.return_value = False
        fake_persona.history_summary.return_value = {}

        with (
            mock.patch.object(
                djview, 'settings',
                types.SimpleNamespace(LLEMON_PERSONA_HOSTS=('opah',)),
            ),
            mock.patch.object(
                djview.discover, 'resolve_path', return_value='/configs/demo.cfg.json',
            ),
            mock.patch.object(djview.os.path, 'exists', return_value=True),
            mock.patch.object(
                djview.discover, 'resolve_history_path', return_value='/hist/demo.jsonl',
            ),
            mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ) as host_url_override,
            mock.patch.object(djview, '_load_persona_config', return_value=mock.Mock()),
            mock.patch.object(djview, '_apply_chat_overrides'),
            mock.patch.object(djview, 'Persona', return_value=fake_persona) as persona_cls,
        ):
            response = view._stream(request)
            list(response.streaming_content)  # drain the generator

        host_url_override.assert_called_once_with('ollama', 'opah')
        persona_cls.assert_called_once()
        self.assertEqual(
            persona_cls.call_args.kwargs['url_override'], 'http://127.0.0.1:15000',
        )
        self.assertTrue(persona_cls.call_args.kwargs['hosted'])

    def test_stream_tunnel_failure_surfaces_as_sse_error(self) -> None:
        """Grove needs no new exception handling for this: a generator's
        body doesn't run until first iterated, so the ConfigError raised
        resolving the host reaches generate()'s own existing outer
        except Exception, which already yields {'error': ...} over SSE."""
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._stream_request(host='opah')

        with (
            mock.patch.object(
                djview, 'settings',
                types.SimpleNamespace(LLEMON_PERSONA_HOSTS=('opah',)),
            ),
            mock.patch.object(
                djview.discover, 'resolve_path', return_value='/configs/demo.cfg.json',
            ),
            mock.patch.object(djview.os.path, 'exists', return_value=True),
            mock.patch.object(
                djview.discover, 'resolve_history_path', return_value='/hist/demo.jsonl',
            ),
            mock.patch.object(
                djview, 'host_url_override',
                side_effect=djview.ConfigError(
                    'SSH connection to opah could not be created.',
                ),
            ),
        ):
            response = view._stream(request)
            chunks = list(response.streaming_content)

        joined = b''.join(chunks).decode()
        self.assertIn('SSH connection to opah could not be created.', joined)

    def test_stream_busy_lock_error_surfaces_as_sse_error(self) -> None:
        """The one-active-hosted-generation gate's ConfigError (raised deep
        inside the real Persona.stream() when discover.host_connection_slot()
        is already held elsewhere) needs no new Grove exception handling:
        because a generator's body does not run until first iterated, it
        reaches generate()'s existing outer except Exception -- the same
        path the tunnel-failure test above exercises -- and is turned into
        the same kind of streamed SSE error."""
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._stream_request(host='opah')
        busy_message = (
            'another hosted ollama/llama.cpp connection is already active; '
            'try again once it finishes.'
        )

        def raising_stream(_user_input):
            raise djview.ConfigError(busy_message)
            yield  # pragma: no cover - unreachable; keeps this a generator fn

        fake_persona = mock.Mock()
        fake_persona.history_path = None
        fake_persona.iterate_history.return_value = iter([])
        fake_persona.stream.side_effect = raising_stream

        with (
            mock.patch.object(
                djview, 'settings',
                types.SimpleNamespace(LLEMON_PERSONA_HOSTS=('opah',)),
            ),
            mock.patch.object(
                djview.discover, 'resolve_path', return_value='/configs/demo.cfg.json',
            ),
            mock.patch.object(djview.os.path, 'exists', return_value=True),
            mock.patch.object(
                djview.discover, 'resolve_history_path', return_value='/hist/demo.jsonl',
            ),
            mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ),
            mock.patch.object(djview, '_load_persona_config', return_value=mock.Mock()),
            mock.patch.object(djview, '_apply_chat_overrides'),
            mock.patch.object(djview, 'Persona', return_value=fake_persona),
        ):
            response = view._stream(request)
            chunks = list(response.streaming_content)

        joined = b''.join(chunks).decode()
        self.assertIn(busy_message, joined)

    def test_stream_unhosted_request_never_calls_host_url_override(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._stream_request()  # no 'host' key at all

        fake_persona = mock.Mock()
        fake_persona.history_path = None
        fake_persona.iterate_history.return_value = iter([])
        fake_persona.stream.return_value = iter([{'content': 'ok', 'done': True}])
        fake_persona.is_e2ee.return_value = False
        fake_persona.is_tee.return_value = False
        fake_persona.has_structured_output.return_value = False
        fake_persona.history_summary.return_value = {}

        with (
            mock.patch.object(
                djview, 'settings', types.SimpleNamespace(LLEMON_PERSONA_HOSTS=()),
            ),
            mock.patch.object(
                djview.discover, 'resolve_path', return_value='/configs/demo.cfg.json',
            ),
            mock.patch.object(djview.os.path, 'exists', return_value=True),
            mock.patch.object(
                djview.discover, 'resolve_history_path', return_value='/hist/demo.jsonl',
            ),
            mock.patch.object(djview, 'host_url_override') as host_url_override,
            mock.patch.object(djview, '_load_persona_config', return_value=mock.Mock()),
            mock.patch.object(djview, '_apply_chat_overrides'),
            mock.patch.object(djview, 'Persona', return_value=fake_persona) as persona_cls,
        ):
            response = view._stream(request)
            list(response.streaming_content)

        host_url_override.assert_not_called()
        self.assertIsNone(persona_cls.call_args.kwargs['url_override'])
        self.assertFalse(persona_cls.call_args.kwargs['hosted'])


class _FakeConfigsSession:
    """Stands in for `Session()` in `configs()` tests.

    `configs()` drives `Session` through `set_type()`/`set_config()`/
    `list_services()`/`set_service()` and reads `config_display`/
    `config_fname`/`config_path` back off it; replacing the whole object
    (rather than mocking the `discover`/`Config` calls the real `Session`
    makes internally to implement those methods) keeps these tests focused
    on `configs()`'s own host-selection logic.
    """

    def __init__(self, *, services=(), config_fname='demo.cfg.json',
                 config_path='/configs/does-not-exist/demo.cfg.json',
                 config_display='Demo Config'):
        self._services = list(services)
        self.config_fname = config_fname
        self.config_path = config_path
        self.config_display = config_display
        self.service_name = None

    def set_type(self, type_id: str) -> None:
        pass

    def set_config(self, path: str) -> None:
        pass

    def list_configs(self):
        return []

    def list_services(self):
        return self._services

    def set_service(self, name: str) -> None:
        self.service_name = name


@unittest.skipIf(djview is None, f'djview import failed: {_IMPORT_ERROR}')
class PersonaDjviewConfigsHostTests(unittest.TestCase):
    """`configs()`'s own host-selection surface: visibility/enablement of
    the Host control through both selection paths, silent clearing of a
    now-incompatible host, hosted model discovery/defaulting, and launch
    gating on a complete manual pair -- see upgrade.md's Task 11 Phase 4
    Grove test list."""

    def _request(self, **overrides) -> types.SimpleNamespace:
        get = {'type': 'chat', 'config': 'demo.cfg.json'}
        get.update(overrides)
        return types.SimpleNamespace(GET=get)

    def _mocks(self, *, persona_hosts=(), services=(), session=None, extra=()):
        fake_session = session or _FakeConfigsSession(services=services)
        return [
            mock.patch.object(
                djview, 'render', side_effect=lambda request, template, context: context,
            ),
            mock.patch.object(djview, 'reverse', return_value='/x/'),
            mock.patch.object(
                djview, 'settings',
                types.SimpleNamespace(LLEMON_PERSONA_HOSTS=persona_hosts),
            ),
            mock.patch.object(djview, 'Session', return_value=fake_session),
            mock.patch.object(
                djview.discover, 'resolve_path', return_value='/configs/demo.cfg.json',
            ),
            mock.patch.object(djview.discover, 'type_descr', return_value='Chat'),
            mock.patch.object(djview.discover, 'config_base', return_value='demo'),
            mock.patch.object(djview.discover, 'list_history_files', return_value=[]),
            mock.patch.object(djview.discover, 'find_start_files', return_value=[]),
            mock.patch.object(djview.discover, 'find_providers', return_value=[]),
            mock.patch.object(djview.discover, 'service_features_label', return_value=''),
            mock.patch.object(
                djview, '_read_config_summary', return_value=('demo', 'Demo Title'),
            ),
            *extra,
        ]

    def _entry(self, context) -> dict:
        return context['configs'][0]

    def _host_row(self, entry: dict) -> dict | None:
        return next(
            (row for row in entry['status_rows'] if row.get('field') == 'Host'), None,
        )

    # -- visibility/enablement through both selection paths ------------ #

    def test_host_row_absent_when_no_persona_hosts_configured(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(service='svc-a')
        services = [('svc-a', 'Service A', 'ollama', 'llama3', [])]
        with contextlib.ExitStack() as stack:
            for cm in self._mocks(persona_hosts=(), services=services):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertFalse(entry['host_capable'])
        self.assertIsNone(self._host_row(entry))

    def test_host_capable_tracks_effective_provider_via_service_selection(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(service='svc-a')
        services = [('svc-a', 'Service A', 'ollama', 'llama3', [])]
        with contextlib.ExitStack() as stack:
            for cm in self._mocks(persona_hosts=('opah',), services=services):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertTrue(entry['host_capable'])
        self.assertIsNotNone(self._host_row(entry))

    def test_host_capable_tracks_effective_provider_via_manual_selection(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='ollama')
        with contextlib.ExitStack() as stack:
            list_models = stack.enter_context(
                mock.patch.object(djview.discover, 'list_models', return_value=[]),
            )
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertTrue(entry['host_capable'])
        self.assertIsNotNone(self._host_row(entry))
        list_models.assert_called_once_with('ollama', url_override=None)

    def test_host_capable_false_for_a_non_hosting_provider(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(service='svc-a')
        services = [('svc-a', 'Service A', 'openai', 'gpt-4', [])]
        with contextlib.ExitStack() as stack:
            for cm in self._mocks(persona_hosts=('opah',), services=services):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertFalse(entry['host_capable'])
        self.assertIsNone(self._host_row(entry))

    def test_no_synthesized_local_choice_among_persona_hosts(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(service='svc-a')
        services = [('svc-a', 'Service A', 'ollama', 'llama3', [])]
        with contextlib.ExitStack() as stack:
            for cm in self._mocks(persona_hosts=('opah', 'aku'), services=services):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertEqual(entry['persona_hosts'], ('opah', 'aku'))

    # -- silent clearing of a now-incompatible host --------------------- #

    def test_manual_provider_change_clears_an_incompatible_host_silently(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='openai', host='opah')
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(djview.discover, 'list_models', return_value=['gpt-4']),
            )
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertEqual(entry['host'], '')
        self.assertFalse(entry['host_capable'])
        self.assertIsNone(context['error'])

    def test_service_selection_clears_an_incompatible_host_silently(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(service='svc-a', host='opah')
        services = [('svc-a', 'Service A', 'openai', 'gpt-4', [])]
        with contextlib.ExitStack() as stack:
            for cm in self._mocks(persona_hosts=('opah',), services=services):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertEqual(entry['host'], '')
        self.assertFalse(entry['host_capable'])
        self.assertIsNone(context['error'])

    # -- hosted model discovery, defaulting, and override --------------- #

    def test_provider_only_hosted_model_discovery_uses_no_url_override(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='ollama')
        with contextlib.ExitStack() as stack:
            host_url_override = stack.enter_context(
                mock.patch.object(djview, 'host_url_override'),
            )
            list_models = stack.enter_context(
                mock.patch.object(djview.discover, 'list_models', return_value=['a']),
            )
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            view.configs(request)
        host_url_override.assert_not_called()
        list_models.assert_called_once_with('ollama', url_override=None)

    def test_provider_and_host_hosted_model_discovery_uses_resolved_url_override(
        self,
    ) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='ollama', host='opah')
        with contextlib.ExitStack() as stack:
            host_url_override = stack.enter_context(mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ))
            list_models = stack.enter_context(mock.patch.object(
                djview.discover, 'list_models', return_value=['a', 'b'],
            ))
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            view.configs(request)
        host_url_override.assert_called_once_with('ollama', 'opah')
        list_models.assert_called_once_with('ollama', url_override='http://127.0.0.1:15000')

    def test_model_list_order_from_discovery_is_preserved(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='ollama', host='opah')
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ))
            stack.enter_context(mock.patch.object(
                djview.discover, 'list_models', return_value=['alpha', 'beta', 'gamma'],
            ))
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertEqual(entry['manual_models'], ['alpha', 'beta', 'gamma'])

    def test_first_model_defaults_when_host_selected_without_an_explicit_model(
        self,
    ) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='ollama', host='opah')
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ))
            stack.enter_context(mock.patch.object(
                djview.discover, 'list_models', return_value=['alpha', 'beta'],
            ))
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertEqual(entry['manual_model'], 'alpha')

    def test_explicit_model_choice_is_respected_over_the_auto_default(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='ollama', host='opah', model='beta')
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ))
            stack.enter_context(mock.patch.object(
                djview.discover, 'list_models', return_value=['alpha', 'beta'],
            ))
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertEqual(entry['manual_model'], 'beta')

    def test_stale_manual_model_not_in_the_discovered_list_is_cleared(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='ollama', host='opah', model='ghost')
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ))
            stack.enter_context(mock.patch.object(
                djview.discover, 'list_models', return_value=['alpha', 'beta'],
            ))
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertEqual(entry['manual_model'], '')

    # -- launch gating and state retention ------------------------------ #

    def test_incomplete_manual_pair_blocks_launch_when_discovery_is_empty(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='ollama', host='opah')
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ))
            stack.enter_context(
                mock.patch.object(djview.discover, 'list_models', return_value=[]),
            )
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertEqual(entry['manual_model'], '')
        selected_init = entry['selected_init']
        self.assertIsNotNone(selected_init)
        self.assertNotIn('url', selected_init)
        self.assertNotIn('chat_params', selected_init)

    def test_host_state_retained_after_failed_model_discovery(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='ollama', host='opah')
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ))
            stack.enter_context(mock.patch.object(
                djview.discover, 'list_models', side_effect=RuntimeError('boom'),
            ))
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertEqual(entry['host'], 'opah')
        self.assertEqual(entry['manual_model_error'], 'boom')

    def test_host_state_retained_after_empty_model_discovery(self) -> None:
        view = djview.LLemonViewSet('llemon_persona', 'llemon_persona')
        request = self._request(provider='ollama', host='opah')
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                djview, 'host_url_override', return_value='http://127.0.0.1:15000',
            ))
            stack.enter_context(
                mock.patch.object(djview.discover, 'list_models', return_value=[]),
            )
            for cm in self._mocks(persona_hosts=('opah',)):
                stack.enter_context(cm)
            context = view.configs(request)
        entry = self._entry(context)
        self.assertEqual(entry['host'], 'opah')
        self.assertEqual(entry['manual_models'], [])
        self.assertIsNone(entry['manual_model_error'])


if __name__ == '__main__':
    unittest.main()

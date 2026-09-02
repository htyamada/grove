import sys
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

from hty7 import sshtunnel
from hty7.llemon.persona import discover
from hty7.llemon.persona.config import ConfigError


def _stash_djview_modules() -> dict:
    """See test_llemon_djview_media_settings.py's helper of the same name:
    importing llemon_djview.persona_host also runs the package's
    __init__.py, so any llemon_djview module already cached by another
    test (against the real django package) must be cleared first."""
    return {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if name == 'llemon_djview' or name.startswith('llemon_djview.')
    }


def _restore_djview_modules(stashed: dict) -> None:
    for name in list(sys.modules):
        if name == 'llemon_djview' or name.startswith('llemon_djview.'):
            del sys.modules[name]
    sys.modules.update(stashed)


def _fake_django_modules() -> dict:
    return {
        'django': types.ModuleType('django'),
        'django.conf': types.SimpleNamespace(
            settings=types.SimpleNamespace(
                LLEMON_IMAGEGEN_MEDIA_DIR='',
                LLEMON_IMAGEGEN_LOG_DIR='',
                LLEMON_VIDEOGEN_MEDIA_DIR='',
                LLEMON_VIDEOGEN_LOG_DIR='',
            ),
        ),
        'django.http': types.SimpleNamespace(
            JsonResponse=object,
            StreamingHttpResponse=object,
        ),
        'django.shortcuts': types.SimpleNamespace(
            redirect=object,
            render=lambda request, template, context: context,
        ),
        'django.urls': types.SimpleNamespace(reverse=lambda *args, **kwargs: ''),
        'django.views': types.ModuleType('django.views'),
        'django.views.decorators': types.ModuleType('django.views.decorators'),
        'django.views.decorators.csrf': types.SimpleNamespace(
            csrf_exempt=lambda f: f,
        ),
        'django.views.decorators.http': types.SimpleNamespace(
            require_POST=lambda f: f,
        ),
        'httpx': types.ModuleType('httpx'),
    }


class PersonaHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stashed = _stash_djview_modules()

    def tearDown(self) -> None:
        _restore_djview_modules(self._stashed)

    def _import_persona_host(self):
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            import llemon_djview.persona_host as persona_host_module
        return persona_host_module

    # -- validate_host_selection ------------------------------------- #

    def test_empty_host_is_always_valid(self) -> None:
        persona_host = self._import_persona_host()
        persona_host.validate_host_selection(('opah', 'aku'), '', 'ollama')

    def test_configured_host_and_supported_provider_is_valid(self) -> None:
        persona_host = self._import_persona_host()
        persona_host.validate_host_selection(('opah', 'aku'), 'opah', 'ollama')

    def test_unconfigured_host_is_rejected(self) -> None:
        persona_host = self._import_persona_host()
        with self.assertRaises(ConfigError):
            persona_host.validate_host_selection(('opah', 'aku'), 'geekom', 'ollama')

    def test_configured_host_with_unsupported_provider_is_rejected(self) -> None:
        persona_host = self._import_persona_host()
        with self.assertRaises(ConfigError):
            persona_host.validate_host_selection(('opah',), 'opah', 'openai')

    def test_case_differing_host_is_rejected_not_folded(self) -> None:
        """Case-differing SSH aliases are administrator misconfiguration,
        not something this validator accommodates (see upgrade.md's Task
        11 scope section)."""
        persona_host = self._import_persona_host()
        with self.assertRaises(ConfigError):
            persona_host.validate_host_selection(('opah',), 'Opah', 'ollama')

    # -- resolve_effective_provider_and_model ------------------------- #

    def test_manual_selection_wins_when_complete(self) -> None:
        persona_host = self._import_persona_host()
        with mock.patch.object(discover, 'get_services') as get_services:
            result = persona_host.resolve_effective_provider_and_model(
                '/cfg.json', 'a-service', 'ollama', 'llama3',
            )
        self.assertEqual(result, ('ollama', 'llama3'))
        get_services.assert_not_called()

    def test_incomplete_manual_selection_falls_back_to_service(self) -> None:
        persona_host = self._import_persona_host()
        with mock.patch.object(
            discover, 'get_services',
            return_value=[('svc', 'display', 'llama.cpp', 'model-x', [])],
        ):
            result = persona_host.resolve_effective_provider_and_model(
                '/cfg.json', 'svc', 'ollama', '',
            )
        self.assertEqual(result, ('llama.cpp', 'model-x'))

    def test_service_not_found_returns_empty(self) -> None:
        persona_host = self._import_persona_host()
        with mock.patch.object(discover, 'get_services', return_value=[]):
            result = persona_host.resolve_effective_provider_and_model(
                '/cfg.json', 'missing-service', '', '',
            )
        self.assertEqual(result, ('', ''))

    def test_no_service_and_no_manual_selection_returns_empty(self) -> None:
        persona_host = self._import_persona_host()
        with mock.patch.object(discover, 'get_services') as get_services:
            result = persona_host.resolve_effective_provider_and_model(
                '/cfg.json', '', '', '',
            )
        self.assertEqual(result, ('', ''))
        get_services.assert_not_called()

    def test_invalid_config_yields_empty_rather_than_raising(self) -> None:
        """discover.get_services() never raises: an invalid config or
        service file yields [] or an "Invalid: ..."-titled row instead of
        an exception, so resolve_effective_provider_and_model() needs no
        try/except of its own."""
        persona_host = self._import_persona_host()
        with mock.patch.object(discover, 'get_services', return_value=[]):
            result = persona_host.resolve_effective_provider_and_model(
                '/does/not/exist.json', 'svc', '', '',
            )
        self.assertEqual(result, ('', ''))

    # -- host_url_override --------------------------------------------- #

    def test_host_url_override_returns_resolved_endpoint(self) -> None:
        persona_host = self._import_persona_host()
        with mock.patch.object(
            discover, 'resolve_host_endpoint', return_value='http://127.0.0.1:15000',
        ) as resolve:
            result = persona_host.host_url_override('ollama', 'aku')
        self.assertEqual(result, 'http://127.0.0.1:15000')
        resolve.assert_called_once_with('ollama', 'aku')

    def test_host_url_override_normalizes_a_tunnel_failure(self) -> None:
        persona_host = self._import_persona_host()
        with mock.patch.object(
            discover, 'resolve_host_endpoint',
            side_effect=sshtunnel.TunnelError('ssh exited: connection refused'),
        ):
            with self.assertRaises(ConfigError) as exc:
                persona_host.host_url_override('ollama', 'aku')
        message = str(exc.exception)
        self.assertEqual(message, 'SSH connection to aku could not be created.')
        # No SSH diagnostics (the underlying TunnelError text) leak into
        # the displayed message.
        self.assertNotIn('connection refused', message)
        # No display-chrome prefix embedded -- see the module docstring
        # for why (Grove's own error-banner rendering adds none; its
        # stream/JS path adds its own).
        self.assertFalse(message.startswith('Error:'))

    def test_host_url_override_propagates_unsupported_provider(self) -> None:
        persona_host = self._import_persona_host()
        with self.assertRaises(ConfigError):
            persona_host.host_url_override('openai', 'aku')


if __name__ == '__main__':
    unittest.main()

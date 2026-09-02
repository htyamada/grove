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


def _stash_djview_modules() -> dict:
    """See test_llemon_djview_media_settings.py's helper of the same name:
    importing llemon_djview.apps also runs the package's __init__.py, so
    any llemon_djview module already cached by another test (against the
    real django package) must be cleared first."""
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


class _FakeAppConfig:
    def __init__(self, *args, **kwargs) -> None:
        pass


def _fake_django_modules() -> dict:
    return {
        'django': types.ModuleType('django'),
        'django.apps': types.SimpleNamespace(AppConfig=_FakeAppConfig),
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


class ReadyAtexitRegistrationTests(unittest.TestCase):
    """AppConfig.ready() must register discover.close_host_tunnels() with
    atexit exactly once per interpreter, regardless of how many times a
    view set, Django app, worker, or test initializer instantiates and
    calls ready() on this AppConfig."""

    def setUp(self) -> None:
        self._stashed = _stash_djview_modules()

    def tearDown(self) -> None:
        _restore_djview_modules(self._stashed)

    def _import_apps(self):
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            import llemon_djview.apps as apps_module
        return apps_module

    def test_ready_registers_close_host_tunnels_once(self) -> None:
        apps_module = self._import_apps()
        # Patch the attribute on the real, already-imported discover module
        # rather than sys.modules: another test in this process may have
        # already imported hty7.llemon.persona for real, in which case
        # `from hty7.llemon.persona import discover` inside ready() resolves
        # via that package's cached `discover` attribute, not a fresh
        # sys.modules lookup — patching sys.modules alone would not be seen.
        with (
            mock.patch(
                'hty7.llemon.persona.discover.close_host_tunnels',
            ) as close_host_tunnels,
            mock.patch.object(apps_module.atexit, 'register') as register,
        ):
            apps_module.LlmPersonaDjviewConfig().ready()
        register.assert_called_once_with(close_host_tunnels)

    def test_ready_is_idempotent_across_multiple_instances_and_calls(self) -> None:
        apps_module = self._import_apps()
        with (
            mock.patch(
                'hty7.llemon.persona.discover.close_host_tunnels',
            ) as close_host_tunnels,
            mock.patch.object(apps_module.atexit, 'register') as register,
        ):
            apps_module.LlmPersonaDjviewConfig().ready()
            apps_module.LlmPersonaDjviewConfig().ready()
            apps_module.LlmPersonaDjviewConfig().ready()
        register.assert_called_once_with(close_host_tunnels)


if __name__ == '__main__':
    unittest.main()

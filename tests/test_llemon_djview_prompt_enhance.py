"""Prompt-enhancement and image-edit-control tests for llemon_djview.

Covers the step-15 Django integration of the prompt-enhancement upgrade:

- media initialization goes through top-level ``mediagen.init()`` and a
  configuration error is fatal — no media backend is initialized after a
  failed init, so no media request can follow;
- image and video generation pass ``generated_prompt`` and
  ``prompt_enhancement`` through to metadata writers, sidecars, and
  summaries, and an enhanced image result forces the client-side canonical
  metadata path;
- every media action requires an explicit provider, and image editing requires
  an explicit live-discovered model with no static/default fallback;
- the image-edit endpoint enforces the per-provider aspect-ratio and size
  policies (OpenRouter: explicit fixed ratio, explicit size forwarded;
  Venice: ``auto`` source ratio, no size accepted).
"""

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


def _stash_djview_modules() -> dict:
    """Remove any already-imported llemon_djview modules from sys.modules."""
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


class FakeJsonResponse:
    def __init__(self, data, status=200):
        self.data = data
        self.status_code = status


def _fake_django_modules():
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
            FileResponse=object,
            Http404=RuntimeError,
            JsonResponse=FakeJsonResponse,
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
    }


class _DjviewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._stashed_djview_modules = _stash_djview_modules()

    def tearDown(self) -> None:
        _restore_djview_modules(self._stashed_djview_modules)


class MediaInitFatalTests(_DjviewTestCase):
    def test_media_settings_uses_top_level_mediagen_init(self) -> None:
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            import llemon_djview as djview
        fake_imagegen = types.SimpleNamespace(
            init=mock.Mock(),
            get_media_dir=mock.Mock(return_value='~/img'),
            get_log_dir=mock.Mock(return_value=''),
        )
        fake_videogen = types.SimpleNamespace(
            init=mock.Mock(),
            get_media_dir=mock.Mock(return_value='~/vid'),
            get_log_dir=mock.Mock(return_value=''),
        )
        fake_mediagen = types.SimpleNamespace(
            init=mock.Mock(),
            imagegen=fake_imagegen,
            videogen=fake_videogen,
        )
        appconfig = object()
        with mock.patch.dict(sys.modules, {'hty7.llemon.mediagen': fake_mediagen}):
            djview.media_settings(appconfig)
        fake_mediagen.init.assert_called_once_with(appconfig)
        # The subpackage inits run inside mediagen.init(); media_settings
        # must not bypass the top-level seam by calling them directly.
        fake_imagegen.init.assert_not_called()
        fake_videogen.init.assert_not_called()

    def test_media_settings_init_error_is_fatal_and_stops_media_setup(self) -> None:
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            import llemon_djview as djview
        fake_imagegen = types.SimpleNamespace(
            init=mock.Mock(),
            get_media_dir=mock.Mock(return_value='~/img'),
            get_log_dir=mock.Mock(return_value=''),
        )
        fake_videogen = types.SimpleNamespace(
            init=mock.Mock(),
            get_media_dir=mock.Mock(return_value='~/vid'),
            get_log_dir=mock.Mock(return_value=''),
        )
        fake_mediagen = types.SimpleNamespace(
            init=mock.Mock(side_effect=RuntimeError('invalid rewrite selector')),
            imagegen=fake_imagegen,
            videogen=fake_videogen,
        )
        with mock.patch.dict(sys.modules, {'hty7.llemon.mediagen': fake_mediagen}):
            with self.assertRaises(RuntimeError):
                djview.media_settings(object())
        # A failed initialization propagates out of the Django settings
        # import, so startup aborts: no media directory is configured and no
        # media backend can serve a request afterwards.
        fake_imagegen.get_media_dir.assert_not_called()
        fake_videogen.get_media_dir.assert_not_called()


class RequiredProviderTests(_DjviewTestCase):
    def test_image_generation_requires_provider(self) -> None:
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
        request = types.SimpleNamespace(body=json.dumps({'prompt': 'draw this'}))
        response = view._generate(request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error'], 'provider is required')

    def test_image_upscale_requires_provider(self) -> None:
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
        request = types.SimpleNamespace(body=json.dumps({'filename': 'a.png'}))
        response = view._do_upscale(request, '/tmp')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error'], 'provider is required')

    def test_video_generation_requires_provider(self) -> None:
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            from llemon_djview.videogen import LLemonVideoGenViewSet

        view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')
        request = types.SimpleNamespace(body=json.dumps({'prompt': 'animate this'}))
        response = view._generate(request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['error'], 'provider is required')


class EditDiscoveryTests(_DjviewTestCase):
    def _metadata(self, side_effect):
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            import llemon_djview.imagegen as imagegen

        list_edit_models = (
            mock.Mock(side_effect=side_effect)
            if isinstance(side_effect, Exception)
            else mock.Mock(return_value=side_effect)
        )
        backend_cls = types.SimpleNamespace(list_edit_models=list_edit_models)
        imagegen._edit_models_cache.clear()
        with mock.patch.object(imagegen, 'supports_edit', return_value=True), \
                mock.patch.object(
                    imagegen, 'make_imagegen_backend', return_value=backend_cls,
                ), mock.patch.object(imagegen.logger, 'warning'):
            return imagegen._edit_metadata('openrouter', 'images')

    def test_empty_discovery_has_no_fallback_model(self) -> None:
        metadata = self._metadata(['', '   ', {}, {'id': ''}])
        self.assertFalse(metadata['supports_edit'])
        self.assertEqual(metadata['edit_models'], [])
        self.assertEqual(metadata['default_edit_model'], '')
        self.assertEqual(metadata['edit_aspect_ratios'], [])
        self.assertEqual(metadata['edit_image_sizes'], [])
        self.assertIn('unavailable', metadata['edit_models_warning'])

    def test_failed_discovery_has_no_fallback_model(self) -> None:
        metadata = self._metadata(RuntimeError('catalog unavailable'))
        self.assertFalse(metadata['supports_edit'])
        self.assertEqual(metadata['edit_models'], [])
        self.assertEqual(metadata['default_edit_model'], '')
        self.assertIn('unavailable', metadata['edit_models_warning'])


class _FakeImageBackend:
    """Minimal imagegen backend double for _generate_result tests."""

    embeds_metadata_in_exif = False
    result: dict = {}
    instances: list = []

    def __init__(self, model=None, log_dir=None):
        self.model = model
        self.shutdown_called = False
        type(self).instances.append(self)

    def generate(self, prompt, **kwargs):
        return dict(type(self).result)

    def shutdown(self):
        self.shutdown_called = True

    @staticmethod
    def write_images(images, save_dir, stamp):
        raise AssertionError('write_images is patched out via save_operation_images')


class ImageEnhancementPassthroughTests(_DjviewTestCase):
    _ENHANCEMENT = {
        'provider': 'openrouter',
        'model': 'text-model',
        'prompt': 'rewrite instruction',
        'request_id': 'req-1',
        'usage': {},
    }

    def _run_generate_result(self, result):
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        backend_cls = _FakeImageBackend
        backend_cls.result = result
        backend_cls.instances = []
        writers = {
            'make_imagegen_backend': mock.Mock(return_value=backend_cls),
            'save_operation_images': mock.Mock(return_value=(['out.png'], 'out.json')),
            'write_image_generation_exif_with_sidecar_fallback':
                mock.Mock(return_value=None),
            'write_image_metadata': mock.Mock(),
            'image_generation_summary_lines': mock.Mock(return_value=[]),
            'model_display': lambda model, *a, **k: model,
        }
        view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(view, '_gallery_dir', return_value=tmp), \
                    mock.patch.object(view, '_log_dir', return_value=''), \
                    mock.patch.object(view, '_ensure_large_thumbnail'), \
                    mock.patch.dict(view._generate_result.__globals__, writers):
                payload, status = view._generate_result(
                    'original prompt', 'image-model', '1:1', '1024x1024',
                    None, None, 'openrouter', 'images',
                )
        return payload, status, writers

    def test_enhanced_result_forces_canonical_metadata_and_summary_line(self) -> None:
        payload, status, writers = self._run_generate_result({
            'model': 'image-model',
            'images': ['fake'],
            'usage': None,
            'generated_prompt': 'rewritten prompt',
            'prompt_enhancement': self._ENHANCEMENT,
        })
        self.assertEqual(status, 200)
        # embeds_metadata_in_exif is False, but the enhanced result must
        # still use the client-side canonical EXIF writer.
        exif_writer = writers['write_image_generation_exif_with_sidecar_fallback']
        exif_writer.assert_called_once()
        kwargs = exif_writer.call_args.kwargs
        self.assertEqual(kwargs['prompt'], 'original prompt')
        self.assertEqual(kwargs['generated_prompt'], 'rewritten prompt')
        self.assertEqual(kwargs['prompt_enhancement'], self._ENHANCEMENT)
        writers['write_image_metadata'].assert_not_called()
        summary_kwargs = writers['image_generation_summary_lines'].call_args.kwargs
        self.assertEqual(summary_kwargs['prompt'], 'original prompt')
        self.assertEqual(summary_kwargs['generated_prompt'], 'rewritten prompt')
        self.assertEqual(payload['generated_prompt'], 'rewritten prompt')

    def test_unenhanced_result_uses_plain_metadata_and_omits_fields(self) -> None:
        payload, status, writers = self._run_generate_result({
            'model': 'image-model',
            'images': ['fake'],
            'usage': None,
        })
        self.assertEqual(status, 200)
        writers['write_image_generation_exif_with_sidecar_fallback'] \
            .assert_not_called()
        metadata_writer = writers['write_image_metadata']
        metadata_writer.assert_called_once()
        self.assertIsNone(metadata_writer.call_args.kwargs['generated_prompt'])
        self.assertIsNone(metadata_writer.call_args.kwargs['prompt_enhancement'])
        summary_kwargs = writers['image_generation_summary_lines'].call_args.kwargs
        self.assertIsNone(summary_kwargs['generated_prompt'])
        self.assertNotIn('generated_prompt', payload)


class _FakeVideoBackend:
    result: dict = {}

    def __init__(self, **kwargs):
        pass

    def generate(self, prompt, **kwargs):
        return dict(type(self).result)

    def shutdown(self):
        pass


class VideoEnhancementPassthroughTests(_DjviewTestCase):
    _ENHANCEMENT = {
        'provider': 'openrouter',
        'model': 'text-model',
        'prompt': 'rewrite instruction',
        'request_id': 'req-2',
        'usage': {},
    }

    def _run_generate(self, result):
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            from llemon_djview.videogen import LLemonVideoGenViewSet

        _FakeVideoBackend.result = result
        sidecar_writer = mock.Mock()
        patches = {
            'normalize_provider_api': lambda *a, **k: ('openrouter', 'openrouter'),
            'default_video_model': lambda *a, **k: 'video-model',
            'default_duration': lambda *a, **k: 5,
            'make_videogen_backend': mock.Mock(return_value=_FakeVideoBackend),
            'save_generated_videos': mock.Mock(return_value=['out.mp4']),
            'write_video_sidecar': sidecar_writer,
            'model_display': lambda model, *a, **k: model,
        }
        view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')
        request = types.SimpleNamespace(
            body=json.dumps({
                'provider': 'openrouter',
                'prompt': 'original prompt',
                'duration': 5,
            }),
        )
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(view, '_gallery_dir', return_value=tmp), \
                    mock.patch.object(view, '_log_dir', return_value=''), \
                    mock.patch.object(view, '_u', return_value=''), \
                    mock.patch.dict(view._generate.__globals__, patches):
                resp = view._generate(request)
        return resp, sidecar_writer

    def test_enhanced_video_sidecar_and_summary_carry_generated_prompt(self) -> None:
        resp, sidecar_writer = self._run_generate({
            'model': 'video-model',
            'videos': ['fake'],
            'generated_prompt': 'rewritten prompt',
            'prompt_enhancement': self._ENHANCEMENT,
        })
        self.assertEqual(resp.status_code, 200)
        meta = sidecar_writer.call_args.args[2]
        self.assertEqual(meta['prompt'], 'original prompt')
        self.assertEqual(meta['generated_prompt'], 'rewritten prompt')
        self.assertEqual(meta['prompt_enhancement'], self._ENHANCEMENT)
        summary = resp.data['summary']
        self.assertIn(['Prompt', 'original prompt'], summary)
        self.assertIn(['Generated prompt', 'rewritten prompt'], summary)
        prompt_index = summary.index(['Prompt', 'original prompt'])
        self.assertEqual(summary[prompt_index + 1],
                         ['Generated prompt', 'rewritten prompt'])

    def test_unenhanced_video_sidecar_omits_enhancement_fields(self) -> None:
        resp, sidecar_writer = self._run_generate({
            'model': 'video-model',
            'videos': ['fake'],
        })
        self.assertEqual(resp.status_code, 200)
        meta = sidecar_writer.call_args.args[2]
        self.assertNotIn('generated_prompt', meta)
        self.assertNotIn('prompt_enhancement', meta)
        summary = resp.data['summary']
        self.assertIn(['Prompt', 'original prompt'], summary)
        self.assertNotIn('Generated prompt', [row[0] for row in summary])


_OPENROUTER_EDIT_META = {
    'edit_models':               ['vendor/edit-model'],
    'edit_models_warning':       None,
    'default_edit_model':        'vendor/edit-model',
    'edit_aspect_ratios':        ['1:1', '16:9'],
    'default_edit_aspect_ratio': '1:1',
    'edit_image_sizes':          ['1024x1024', '2048x2048'],
    'default_edit_image_size':   '1024x1024',
}

_VENICE_EDIT_META = {
    'edit_models':               ['qwen-edit'],
    'edit_models_warning':       None,
    'default_edit_model':        'qwen-edit',
    'edit_aspect_ratios':        ['auto', '1:1'],
    'default_edit_aspect_ratio': 'auto',
    'edit_image_sizes':          [],
    'default_edit_image_size':   '',
}


class ImageEditControlTests(_DjviewTestCase):
    def _run_edit(self, body, edit_meta, provider='openrouter', *, include_provider=True):
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
        request_body = dict(body)
        if include_provider:
            request_body['provider'] = provider
        request = types.SimpleNamespace(body=json.dumps(request_body))
        edit_result = mock.Mock(return_value=({'files': ['out.png']}, 200))
        patches = {
            'normalize_provider_api': lambda *a, **k: (provider, provider),
            'supports_edit': lambda *a, **k: True,
            '_edit_metadata': lambda *a, **k: dict(edit_meta),
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                        view, '_read_image_as_data_url',
                        return_value=('data:image/png;base64,x', None),
                    ), \
                    mock.patch.object(view, '_edit_result', edit_result), \
                    mock.patch.dict(view._do_edit_image.__globals__, patches):
                resp = view._do_edit_image(request, tmp)
        return resp, edit_result

    def test_openrouter_edit_requires_explicit_fixed_aspect_ratio(self) -> None:
        resp, edit_result = self._run_edit(
            {'filename': 'a.png', 'prompt': 'change it',
             'model': 'vendor/edit-model'},
            _OPENROUTER_EDIT_META,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('fixed aspect ratio', resp.data['error'])
        edit_result.assert_not_called()

    def test_edit_requires_explicit_provider(self) -> None:
        resp, edit_result = self._run_edit(
            {'filename': 'a.png', 'prompt': 'change it',
             'model': 'vendor/edit-model', 'aspect_ratio': '1:1'},
            _OPENROUTER_EDIT_META,
            include_provider=False,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['error'], 'provider is required')
        edit_result.assert_not_called()

    def test_edit_requires_explicit_model(self) -> None:
        resp, edit_result = self._run_edit(
            {'filename': 'a.png', 'prompt': 'change it', 'aspect_ratio': '1:1'},
            _OPENROUTER_EDIT_META,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data['error'], 'edit model is required')
        edit_result.assert_not_called()

    def test_no_discovered_models_disables_edit(self) -> None:
        no_models = dict(_OPENROUTER_EDIT_META)
        no_models.update({
            'supports_edit': False,
            'edit_models': [],
            'default_edit_model': '',
            'edit_aspect_ratios': [],
            'default_edit_aspect_ratio': '',
            'edit_image_sizes': [],
            'default_edit_image_size': '',
        })
        resp, edit_result = self._run_edit(
            {'filename': 'a.png', 'prompt': 'change it',
             'model': 'vendor/edit-model'},
            no_models,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no edit models are available', resp.data['error'])
        edit_result.assert_not_called()

    def test_openrouter_edit_forwards_explicit_size(self) -> None:
        resp, edit_result = self._run_edit(
            {'filename': 'a.png', 'prompt': 'change it',
             'model': 'vendor/edit-model', 'aspect_ratio': '16:9',
             'image_size': '2048x2048'},
            _OPENROUTER_EDIT_META,
        )
        self.assertEqual(resp.status_code, 200)
        args = edit_result.call_args.args
        self.assertEqual(args[5], '16:9')        # aspect_ratio
        self.assertEqual(args[6], '2048x2048')   # image_size

    def test_openrouter_edit_defaults_size_when_omitted(self) -> None:
        resp, edit_result = self._run_edit(
            {'filename': 'a.png', 'prompt': 'change it',
             'model': 'vendor/edit-model', 'aspect_ratio': '1:1'},
            _OPENROUTER_EDIT_META,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(edit_result.call_args.args[6], '1024x1024')

    def test_openrouter_edit_rejects_invalid_size(self) -> None:
        resp, edit_result = self._run_edit(
            {'filename': 'a.png', 'prompt': 'change it',
             'model': 'vendor/edit-model', 'aspect_ratio': '1:1',
             'image_size': '640x480'},
            _OPENROUTER_EDIT_META,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('image_size', resp.data['error'])
        edit_result.assert_not_called()

    def test_openrouter_edit_rejects_unknown_model(self) -> None:
        resp, edit_result = self._run_edit(
            {'filename': 'a.png', 'prompt': 'change it',
             'model': 'vendor/not-an-edit-model', 'aspect_ratio': '1:1'},
            _OPENROUTER_EDIT_META,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('edit model', resp.data['error'])
        edit_result.assert_not_called()

    def test_venice_edit_defaults_to_auto_source_ratio(self) -> None:
        resp, edit_result = self._run_edit(
            {'filename': 'a.png', 'prompt': 'change it', 'model': 'qwen-edit'},
            _VENICE_EDIT_META,
            provider='venice',
        )
        self.assertEqual(resp.status_code, 200)
        args = edit_result.call_args.args
        self.assertEqual(args[5], 'auto')   # aspect_ratio
        self.assertIsNone(args[6])          # image_size never sent to Venice

    def test_venice_edit_rejects_explicit_size_with_explanation(self) -> None:
        resp, edit_result = self._run_edit(
            {'filename': 'a.png', 'prompt': 'change it', 'model': 'qwen-edit',
             'image_size': '1024x1024'},
            _VENICE_EDIT_META,
            provider='venice',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('source image', resp.data['error'])
        edit_result.assert_not_called()


class EditResultBackendForwardingTests(_DjviewTestCase):
    def _run_edit_result(self, image_size):
        with mock.patch.dict(sys.modules, _fake_django_modules()):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        recorded: dict = {}

        class FakeEditBackend:
            def __init__(self, model=None, log_dir=None):
                pass

            def edit(self, image, prompt, **kwargs):
                recorded.update(kwargs)
                return {'images': ['fake'], 'usage': None}

            def shutdown(self):
                pass

        view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
        save_result = mock.Mock(return_value=({'files': ['out.png']}, 200))
        patches = {'make_imagegen_backend': mock.Mock(return_value=FakeEditBackend)}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(view, '_log_dir', return_value=''), \
                    mock.patch.object(view, '_save_operation_result', save_result), \
                    mock.patch.dict(view._edit_result.__globals__, patches):
                payload, status = view._edit_result(
                    'data:image/png;base64,x', 'a.png', tmp, 'change it',
                    'vendor/edit-model', '1:1', image_size, None,
                    'openrouter', 'images',
                )
        sidecar = save_result.call_args.args[3]
        return recorded, sidecar

    def test_explicit_size_reaches_backend_and_sidecar(self) -> None:
        recorded, sidecar = self._run_edit_result('2048x2048')
        self.assertEqual(recorded['image_size'], '2048x2048')
        self.assertEqual(sidecar['image_size'], '2048x2048')

    def test_omitted_size_is_not_sent_to_backend(self) -> None:
        recorded, sidecar = self._run_edit_result(None)
        self.assertNotIn('image_size', recorded)
        self.assertNotIn('image_size', sidecar)


if __name__ == '__main__':
    unittest.main()

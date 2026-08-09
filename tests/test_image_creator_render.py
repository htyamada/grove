"""End-to-end render test for the LLemon image-creator template.

Unlike the context-only tests in tests/test_llemon_djview_media_settings.py
(which fake Django and stub render), this configures a real Django template
engine and renders llemon_image/image.html for real, so the provider-reactive
edit metadata wiring (json_script blocks + the edit dropdowns / Type options)
is validated as actual HTML output.
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1] / 'lib'      # .../grove/lib
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from django.conf import settings
except ModuleNotFoundError:
    settings = None


if settings is None:
    class ImageCreatorRenderTests(unittest.TestCase):
        @unittest.skip('django is not installed')
        def test_django_required(self) -> None:
            pass
else:
    _base_root = Path(tempfile.mkdtemp(prefix='llemon-image-render-'))
    (_base_root / 'base').mkdir(parents=True, exist_ok=True)
    (_base_root / 'base' / 'base.html').write_text(
        '{% block extra_head %}{% endblock %}'
        '{% block heading %}{% endblock %}'
        '{% block content %}{% endblock %}',
        encoding='utf-8',
    )
    _llemon_templates = ROOT / 'llemon_djview' / 'templates'

    _TEMPLATES = [{
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [str(_base_root), str(_llemon_templates)],
        'APP_DIRS': False,
        'OPTIONS': {},
    }]

    # This file's settings.configure() call only takes effect when Django
    # settings are not already configured — another test file discovered in
    # the same process (e.g. tests/test_djview.py) may have configured its
    # own ROOT_URLCONF/TEMPLATES/INSTALLED_APPS first, since Django settings
    # can only be configured once per process. The bare-minimum call below
    # covers the standalone-run case; _DJANGO_TEST_OVERRIDES below covers
    # this test's own values regardless of what ran first, via
    # override_settings, which Django supports even after settings are
    # already configured and correctly invalidates the URL/template/app
    # caches for the duration of the override.
    if not settings.configured:
        settings.configure(
            SECRET_KEY='test-secret',
            ROOT_URLCONF=__name__,
            ALLOWED_HOSTS=['*'],
            INSTALLED_APPS=[
                'django.contrib.contenttypes',
                'django.contrib.sessions',
            ],
            TEMPLATES=_TEMPLATES,
            MIDDLEWARE=[],
        )

    import django

    django.setup()

    from django.http import HttpResponse
    from django.test import RequestFactory
    from django.test.utils import override_settings
    from django.template.loader import get_template
    from django.urls import include, path

    from llemon_djview.imagegen import LLemonImageGenViewSet
    from llemon_djview.videogen import LLemonVideoGenViewSet

    def _noop(request, *args, **kwargs):
        return HttpResponse('')

    _img_patterns = ([
        path('', _noop, name='image_creator'),
        path('gallery/', _noop, name='gallery'),
        path('generate/', _noop, name='generate'),
        path('model-note/', _noop, name='model_note'),
        path('models-json/', _noop, name='models_json'),
        path('file/<str:filename>', _noop, name='image_file'),
        path('thumb-large/<str:filename>', _noop, name='large_thumbnail'),
        path('upscale/', _noop, name='upscale'),
        path('edit/', _noop, name='edit_image'),
    ], 'llemon_image')
    urlpatterns = [path('', include(_img_patterns, namespace='llemon_image'))]

    _DJANGO_TEST_OVERRIDES = dict(
        ROOT_URLCONF=__name__,
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.sessions',
        ],
        TEMPLATES=_TEMPLATES,
        MIDDLEWARE=[],
    )

    _PROVIDER_CONFIG = {
        'supports_temperature':   False,
        'supports_system_prompt': False,
        'extra_fields': [
            {'name': 'style_preset', 'label': 'Style preset', 'type': 'select',
             'choices': [['', '(none)'], ['Pixel Art', 'Pixel Art']]},
        ],
    }

    class ImageCreatorRenderTests(unittest.TestCase):
        def setUp(self) -> None:
            self.factory = RequestFactory()
            self.view = LLemonImageGenViewSet('llemon_image', 'llemon_image')

        def _render(self):
            request = self.factory.get('/')
            overrides = {
                'normalize_provider_api':        lambda p=None, a=None: ('venice', 'generation'),
                'list_image_models_with_metadata': mock.Mock(
                    return_value=[{'id': 'm1', 'name': 'Model One', 'description': 'desc'}]),
                'model_quirk_labels':            mock.Mock(return_value=[]),
                'default_system_prompt':         mock.Mock(return_value=None),
                'model_capabilities':            mock.Mock(return_value={}),
                'get_model_tag_states':          mock.Mock(return_value={}),
                'get_notes_load_errors':         mock.Mock(return_value=[]),
                'get_tags':                      mock.Mock(return_value=[]),
                'get_reverse_tags':              mock.Mock(return_value=[]),
                'get_notes_slot':                mock.Mock(return_value=''),
                'aspect_ratios':                 mock.Mock(return_value=['1:1']),
                'image_sizes':                   mock.Mock(return_value=['1K']),
                'default_aspect_ratio':          mock.Mock(return_value='1:1'),
                'default_image_size':            mock.Mock(return_value='1K'),
                'default_image_model':           mock.Mock(return_value='m1'),
                '_provider_config':              mock.Mock(return_value=_PROVIDER_CONFIG),
                'PROVIDERS':                     ['venice', 'openrouter'],
                'supports_edit':                 mock.Mock(return_value=True),
                'supports_upscale':              mock.Mock(return_value=True),
                '_edit_metadata':                 mock.Mock(return_value={
                    'supports_edit':             True,
                    'edit_models':               ['firered-image-edit', 'qwen-edit'],
                    'edit_models_warning':       None,
                    'default_edit_model':        'firered-image-edit',
                    'edit_aspect_ratios':        ['auto', '1:1', '16:9'],
                    'default_edit_aspect_ratio': 'auto',
                    'edit_image_sizes':          [],
                    'default_edit_image_size':   '',
                }),
            }
            with override_settings(**_DJANGO_TEST_OVERRIDES):
                with mock.patch.dict(self.view.image_creator.__globals__, overrides):
                    with mock.patch.object(self.view, '_gallery_picker_items', return_value=[]):
                        return self.view.image_creator(request)

        def _assert_javascript_syntax(self, html: str) -> None:
            scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
            self.assertTrue(scripts)
            for source in scripts:
                result = subprocess.run(
                    ['node', '--check'], input=source, text=True,
                    capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

        def test_image_creator_renders_provider_reactive_edit_metadata(self) -> None:
            response = self._render()
            self.assertEqual(response.status_code, 200)
            html = response.content.decode('utf-8')
            self._assert_javascript_syntax(html)

            self.assertIn('id="creator-presentation-data"', html)
            self.assertNotIn('id="model-options-data"', html)

            # Edit metadata is serialized for the JS.
            self.assertIn('firered-image-edit', html)
            self.assertIn('qwen-edit', html)

            # Server-rendered edit-model dropdown reflects the provider's models.
            self.assertIn('<option value="firered-image-edit" selected>', html)

            # Provider-capability Type options are present (Upscale + Edit).
            self.assertIn('<option value="upscale">Upscale</option>', html)
            self.assertIn('<option value="edit">Edit</option>', html)

            # The provider-switch handler that repopulates edit options is wired in.
            self.assertIn('_applyEditMetadata', html)
            self.assertIn('createMediaRefreshController', html)
            self.assertIn('selectImageModel', html)

            # Provider-dependent actions carry the provider selected in the UI.
            self.assertIn(
                'filename: sourceImageFname,\n      provider: currentProvider,\n'
                '      scale:',
                html,
            )
            self.assertIn(
                'filename: sourceImageFname,\n      provider: currentProvider,\n'
                '      prompt:',
                html,
            )

        def test_image_refresh_separates_discovery_from_selected_target_lookup(self) -> None:
            request = self.factory.get(
                '/models-json/?provider=venice&selected_model=m2'
            )
            list_models = mock.Mock(return_value=[
                {'id': 'm1', 'name': 'One', 'description': 'one'},
                {'id': 'm2', 'name': 'Two', 'description': 'two'},
            ])
            capabilities = mock.Mock(return_value={
                'qualities': ['high'], 'default_quality': 'high',
            })
            overrides = {
                'normalize_provider_api': mock.Mock(return_value=('venice', 'generation')),
                'list_image_models_with_metadata': list_models,
                'model_capabilities': capabilities,
                'aspect_ratios': mock.Mock(return_value=['1:1']),
                'image_sizes': mock.Mock(return_value=['1K']),
                'default_aspect_ratio': mock.Mock(return_value='1:1'),
                'default_image_size': mock.Mock(return_value='1K'),
                'default_image_model': mock.Mock(return_value='m1'),
                '_provider_config': mock.Mock(return_value={}),
                'supports_edit': mock.Mock(return_value=False),
                'supports_upscale': mock.Mock(return_value=False),
                '_edit_metadata': mock.Mock(return_value={
                    'supports_edit': False,
                    'edit_models': [],
                    'edit_models_warning': None,
                    'default_edit_model': '',
                    'edit_aspect_ratios': [],
                    'default_edit_aspect_ratio': '',
                    'edit_image_sizes': [],
                    'default_edit_image_size': '',
                }),
            }
            with mock.patch.dict(self.view._models_json.__globals__, overrides):
                with mock.patch.object(self.view, '_model_tag_states', return_value={}):
                    provider_response = self.view._models_json(request)

                    target_request = self.factory.get(
                        '/models-json/?provider=venice&target=generate&model=m1'
                    )
                    target_response = self.view._models_json(target_request)

            list_models.assert_called_once()
            self.assertEqual(capabilities.call_count, 2)
            provider_data = json.loads(provider_response.content)
            self.assertEqual(provider_data['operations']['generate']['selected_model'], 'm2')
            self.assertEqual(
                json.loads(target_response.content)['target']['model'], 'm1',
            )
            self.assertEqual(
                [call.args[0] for call in capabilities.call_args_list],
                ['m2', 'm1'],
            )

        def test_video_refresh_returns_one_nonduplicated_contract(self) -> None:
            view = LLemonVideoGenViewSet('llemon_video', 'llemon_image')
            request = self.factory.get(
                '/models-json/?provider=venice&selected_model=m2'
            )
            model_options = [
                {'id': 'm1', 'display': 'One', 'capabilities': {'durations': ['5s']}},
                {'id': 'm2', 'display': 'Two', 'capabilities': {'durations': ['10s']}},
            ]
            with mock.patch.object(view, '_model_options', return_value=model_options) as listed:
                with mock.patch.object(view, '_model_tag_states', return_value={}):
                    with mock.patch.dict(view._models_json.__globals__, {
                        'normalize_provider_api': mock.Mock(
                            return_value=('venice', 'generation'),
                        ),
                        'default_video_model': mock.Mock(return_value='m1'),
                        'default_duration': mock.Mock(return_value='5s'),
                    }):
                        response = view._models_json(request)

            listed.assert_called_once()
            data = json.loads(response.content)
            operation = data['operations']['generate']
            self.assertEqual(operation['selected_model'], 'm2')
            self.assertEqual(len(operation['model_options']), 2)
            self.assertNotIn('model_options', data)
            self.assertNotIn('model_capabilities', operation['controls'])

        def test_video_creator_consumes_valid_presentation_javascript(self) -> None:
            presentation = {
                'provider': 'venice',
                'api': 'generation',
                'target': {
                    'provider': 'venice', 'api': 'generation',
                    'operation': 'provider', 'model': None,
                },
                'operations': {'generate': {
                    'operation': 'generate',
                    'model_options': [{
                        'id': 'm1', 'display': 'Model One', 'description': 'desc',
                        'capabilities': {'presentation': {
                            'mode': 'text-to-video',
                            'reference_image_request_family': 'none',
                            'allows_start_image': False,
                            'allows_end_image': False,
                            'allows_reference_images': False,
                            'shows_scene_images': False,
                            'is_upscale': False,
                        }},
                    }],
                    'selected_model': 'm1',
                    'default_model': 'm1',
                    'defaults': {'duration': '5s'},
                    'controls': {},
                    'availability': {'enabled': True},
                    'selected_target': {'target': {
                        'provider': 'venice', 'api': 'generation',
                        'operation': 'generate', 'model': 'm1',
                    }, 'controls': {}},
                    'notes': {'provider': 'venice', 'model_tag_states': {}},
                }},
            }
            context = {
                'site_name': 'Test', 'title': 'Video Creator',
                'providers': ['venice'], 'provider': 'venice',
                'model_options': presentation['operations']['generate']['model_options'],
                'model_tag_states': {}, 'reverse_tags': [],
                'presentation': presentation,
                'default_model': 'm1', 'default_duration': '5s',
                'available_tags': [], 'gallery_images': [],
                'output_subdir': '',
            }
            with override_settings(**_DJANGO_TEST_OVERRIDES):
                html = get_template('llemon_video/video.html').render(context)
            self.assertIn('id="creator-presentation-data"', html)
            self.assertNotIn('id="model-options-data"', html)
            self.assertIn('selectVideoModel', html)
            self._assert_javascript_syntax(html)


if __name__ == '__main__':
    unittest.main()

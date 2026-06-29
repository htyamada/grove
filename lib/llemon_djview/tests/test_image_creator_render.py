"""End-to-end render test for the LLemon image-creator template.

Unlike the context-only tests in tests/test_llemon_djview_media_settings.py
(which fake Django and stub render), this configures a real Django template
engine and renders llemon_image/image.html for real, so the provider-reactive
edit metadata wiring (json_script blocks + the edit dropdowns / Type options)
is validated as actual HTML output.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]      # .../grove/lib
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
    from django.urls import include, path

    from llemon_djview.imagegen import LLemonImageGenViewSet

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
                'edit_models':                   mock.Mock(
                    return_value=['firered-image-edit', 'qwen-edit']),
                'default_edit_model':            mock.Mock(return_value='firered-image-edit'),
                'edit_aspect_ratios':            mock.Mock(return_value=['auto', '1:1', '16:9']),
            }
            with mock.patch.dict(self.view.image_creator.__globals__, overrides):
                with mock.patch.object(self.view, '_gallery_picker_items', return_value=[]):
                    return self.view.image_creator(request)

        def test_image_creator_renders_provider_reactive_edit_metadata(self) -> None:
            response = self._render()
            self.assertEqual(response.status_code, 200)
            html = response.content.decode('utf-8')

            # json_script blocks feeding the initial provider cache.
            for elem_id in (
                'supports-edit-data', 'supports-upscale-data', 'edit-models-data',
                'default-edit-model-data', 'edit-aspect-ratios-data',
            ):
                self.assertIn(f'id="{elem_id}"', html)

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


if __name__ == '__main__':
    unittest.main()

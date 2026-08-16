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
from dataclasses import dataclass
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

    import llemon_djview.imagegen as imagegen_view
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

    def _presentation(model_id, *, generate=True, edit=False):
        operation = lambda available: {
            'available': available, 'unavailable_reason': None if available else 'unavailable',
            'designation': None, 'designation_reason': None,
        }
        controls = lambda: {
            'aspect_ratios': ['1:1'], 'default_aspect_ratio': '1:1',
            'image_sizes': ['1K'], 'default_image_size': '1K',
            'qualities': [], 'default_quality': None, 'extra_fields': [],
        }
        return {
            'id': model_id, 'name': model_id, 'description': None,
            'detail': 'complete',
            'operations': {'generate': operation(generate), 'edit': operation(edit)},
            'controls': {'generate': controls(), 'edit': controls()},
            'edit_input': {
                'accepted_source_kinds': ['data_url'] if edit else [],
                'required_backend_transports': {},
                'available_backend_transports': [],
            },
        }

    class ImageCreatorRenderTests(unittest.TestCase):
        def setUp(self) -> None:
            self.factory = RequestFactory()
            self.view = LLemonImageGenViewSet('llemon_image', 'llemon_image')

        def test_selection_keeps_nullable_default_and_provisionally_selects_summary(self) -> None:
            summary = _presentation('summary-model')
            summary['detail'] = 'summary'
            summary['operations']['generate'] = {
                'available': False,
                'unavailable_reason': 'model detail has not been resolved',
                'designation': None,
                'designation_reason': None,
            }
            rows = [{'id': 'summary-model', 'presentation': summary}]
            self.assertEqual(
                imagegen_view._select_model(rows, 'generate', None, None),
                'summary-model',
            )

        def test_data_url_compatibility_disables_but_preserves_edit_row(self) -> None:
            row = {'id': 'remote-only', 'presentation': _presentation(
                'remote-only', generate=False, edit=True,
            )}
            row['presentation']['edit_input']['accepted_source_kinds'] = ['https_url']
            eligible, enabled, reason = imagegen_view._operation_state(
                row, 'edit', source_kind='data_url',
            )
            self.assertFalse(eligible)
            self.assertFalse(enabled)
            self.assertEqual(reason, 'data URL unsupported')
            self.assertEqual(imagegen_view._model_options([row])[0]['id'], 'remote-only')

        def test_selection_precedence_and_stale_request_fallback(self) -> None:
            rows = [
                {'id': model_id, 'presentation': _presentation(model_id)}
                for model_id in ('first', 'default', 'requested')
            ]
            self.assertEqual(
                imagegen_view._select_model(rows, 'generate', 'requested', 'default'),
                'requested',
            )
            self.assertEqual(
                imagegen_view._select_model(rows, 'generate', 'stale', 'default'),
                'default',
            )
            self.assertEqual(
                imagegen_view._select_model(rows, 'generate', 'stale', None),
                'first',
            )

        def test_operation_state_covers_unavailable_and_required_transport(self) -> None:
            unavailable = {'id': 'off', 'presentation': _presentation('off')}
            unavailable['presentation']['operations']['generate'] = {
                'available': False, 'unavailable_reason': 'disabled by provider',
                'designation': None, 'designation_reason': None,
            }
            self.assertEqual(
                imagegen_view._operation_state(unavailable, 'generate'),
                (False, False, 'disabled by provider'),
            )
            transport = {'id': 'upload', 'presentation': _presentation(
                'upload', generate=False, edit=True,
            )}
            transport['presentation']['edit_input'].update({
                'accepted_source_kinds': ['data_url'],
                'required_backend_transports': {'data_url': 'provider_upload'},
                'available_backend_transports': [],
            })
            self.assertEqual(
                imagegen_view._operation_state(
                    transport, 'edit', source_kind='data_url',
                ),
                (False, False, 'required transport unavailable'),
            )

        def test_model_options_are_independent_complete_copies(self) -> None:
            row = {'id': 'm1', 'name': 'One', 'extra': {'value': 1},
                   'presentation': _presentation('m1')}
            option = imagegen_view._model_options([row])[0]
            option['extra']['value'] = 2
            option['presentation']['name'] = 'Changed'
            self.assertEqual(row['extra']['value'], 1)
            self.assertEqual(row['presentation']['name'], 'm1')

        def test_notice_serialization_preserves_fields_and_order(self) -> None:
            @dataclass(frozen=True)
            class Notice:
                code: str = 'model_info_incomplete'
                level: str = 'warning'
                message: str = 'Incomplete.'
                provider: str = 'example'
                scope: str = 'batch'
                source_url: str | None = None
                model: str | None = None
                models: tuple[str, ...] = ('a', 'b')
                retrieved_at: str | None = None
                cause: str | None = None
            notices = [
                Notice(message='First.', models=('a', 'b')),
                Notice(message='Second.', models=('c',)),
            ]
            serialized = [imagegen_view._notice_dict(notice) for notice in notices]
            self.assertEqual(
                [notice['message'] for notice in serialized], ['First.', 'Second.'],
            )
            self.assertEqual(serialized[0]['level'], 'warning')
            self.assertEqual(serialized[0]['models'], ['a', 'b'])

        def _render(self, **extra_overrides):
            request = self.factory.get('/')
            overrides = {
                'normalize_provider_api':        lambda p=None, a=None: ('venice', 'generation'),
                'list_image_models_with_metadata': mock.Mock(
                    return_value=[{'id': 'm1', 'name': 'Model One', 'description': 'desc',
                                   'presentation': _presentation('m1')}]),
                'model_quirk_labels':            mock.Mock(return_value=[]),
                'default_system_prompt':         mock.Mock(return_value=None),
                'model_presentation':            mock.Mock(side_effect=lambda m, *a, **k: _presentation(m)),
                'get_model_tag_states':          mock.Mock(return_value={}),
                'get_notes_load_errors':         mock.Mock(return_value=[]),
                'get_tags':                      mock.Mock(return_value=[]),
                'get_reverse_tags':              mock.Mock(return_value=[]),
                'get_notes_slot':                mock.Mock(return_value=''),
                'aspect_ratios':                 mock.Mock(return_value=['1:1']),
                'image_sizes':                   mock.Mock(return_value=['1K']),
                'default_aspect_ratio':          mock.Mock(return_value='1:1'),
                'default_image_size':            mock.Mock(return_value='1K'),
                'default_model_for_presentation': mock.Mock(return_value='m1'),
                '_provider_config':              mock.Mock(return_value=_PROVIDER_CONFIG),
                'PROVIDERS':                     ['venice', 'openrouter'],
                'supports_edit':                 mock.Mock(return_value=True),
                'supports_upscale':              mock.Mock(return_value=True),
                '_edit_metadata':                 mock.Mock(return_value={
                    'supports_edit':             True,
                    'edit_models':               ['firered-image-edit', 'qwen-edit'],
                    'edit_model_options':        [
                        {'id': 'firered-image-edit', 'name': 'FireRed',
                         'presentation': _presentation('firered-image-edit', generate=False, edit=True),
                         'display': 'FireRed (firered-image-edit)'},
                        {'id': 'qwen-edit', 'name': 'Qwen',
                         'presentation': _presentation('qwen-edit', generate=False, edit=True),
                         'display': 'Qwen (qwen-edit)'},
                    ],
                    'selected_edit_model':       'firered-image-edit',
                    'default_edit_model':        'firered-image-edit',
                    'edit_aspect_ratios':        ['auto', '1:1', '16:9'],
                    'default_edit_aspect_ratio': 'auto',
                    'edit_image_sizes':          [],
                    'default_edit_image_size':   '',
                }),
            }
            overrides.update(extra_overrides)
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
            self.assertIn('id="creator-notices-data"', html)
            self.assertIn('id="model-info-notices"', html)
            self.assertNotIn('id="model-options-data"', html)
            self.assertIn('selectedActionAvailability', html)
            self.assertIn('generationOptionState', html)
            self.assertIn("button.disabled = availability.enabled !== true", html)
            self.assertIn("? 'Warning: ' + notice.message", html)
            self.assertIn("noticeMsg.classList.remove('warning')", html)
            self.assertIn("begin: function () { renderModelInfoNotices([]); }", html)
            self.assertRegex(
                html,
                r'begin: function \(target\) \{\s*'
                r'if \(!target\.preserve_notices\) renderModelInfoNotices\(\[\]\);',
            )
            self.assertIn('selectImageModel(modelSel.value, true)', html)
            self.assertIn('let currentProviderData = initialProviderData', html)
            self.assertIn('let ACTIVE_GENERATE_DEFAULTS =', html)
            self.assertIn('mediaSetVisibleChoiceControl', html)
            self.assertIn('mediaVisibleChoiceControlValue', html)
            self.assertIn('mediaAfterTargetReady(targetReady, function ()', html)
            self.assertIn('mediaApplyChoiceControl(', html)
            self.assertNotIn('DEFAULTS.aspect_ratio = controls.', html)
            self.assertNotIn('{...initialProviderData, edit_models:', html)
            self.assertIn(
                '_renderModelOptions(data.selected_model, data.selected_model != null)',
                html,
            )
            self.assertIn(
                'eligibleOptions = options.filter(opt => '
                'generationOptionState(opt).eligible)',
                html,
            )
            self.assertRegex(
                html,
                r'operation\.selected_model = modelId;\s*'
                r'operation\.selected_target = null;\s*'
                r'operation\.availability = \{\s*'
                r'enabled: false,',
            )
            self.assertRegex(
                html,
                r"reason: 'no eligible generation model',\s*\};\s*"
                r"updateDescription\(''\);\s*notesArea\.value = '';\s*"
                r'updateActionAvailability\(\);',
            )
            self.assertRegex(
                html,
                r'_applyProviderData\(imageProviderData\(presentation\)\);\s*'
                r'updateActionAvailability\(\);',
            )
            self.assertRegex(
                html,
                r"const btn\s*= document\.getElementById\('generate-btn'\);\s*"
                r'updateActionAvailability\(\);',
            )

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

        def test_image_creator_returns_502_when_edit_discovery_fails(self) -> None:
            # Edit-model discovery is live and has no fallback: a provider
            # that declares edit support and then cannot be listed is an
            # upstream fault, so the creator reports it instead of rendering
            # a page with editing silently degraded.
            response = self._render(_edit_metadata=mock.Mock(
                side_effect=RuntimeError('catalog unavailable'),
            ))
            self.assertEqual(response.status_code, 502)
            self.assertEqual(
                json.loads(response.content)['error'],
                'could not list edit models: catalog unavailable',
            )

        def test_selected_summary_resolves_once_and_can_remain_disabled(self) -> None:
            summary = _presentation('m1')
            summary['detail'] = 'summary'
            summary['operations']['generate'].update({
                'available': False,
                'unavailable_reason': 'model detail has not been resolved',
            })
            detail = _presentation('m1')
            detail['operations']['generate'].update({
                'available': False,
                'unavailable_reason': 'not enabled for this account',
            })
            lookup = mock.Mock(return_value=detail)
            response = self._render(
                list_image_models_with_metadata=mock.Mock(return_value=[{
                    'id': 'm1', 'name': 'One', 'description': 'one',
                    'presentation': summary,
                }]),
                model_presentation=lookup,
            )
            self.assertEqual(response.status_code, 200)
            lookup.assert_called_once()
            html = response.content.decode()
            presentation_text = re.search(
                r'<script id="creator-presentation-data" type="application/json">(.*?)</script>',
                html,
            ).group(1)
            presentation = json.loads(presentation_text)
            operation = presentation['operations']['generate']
            self.assertEqual(operation['selected_model'], 'm1')
            self.assertFalse(operation['availability']['enabled'])
            self.assertEqual(
                operation['availability']['reason'], 'not enabled for this account',
            )

        def test_selected_summary_resolves_once_and_can_become_available(self) -> None:
            summary = _presentation('m1')
            summary['detail'] = 'summary'
            summary['operations']['generate'].update({
                'available': False,
                'unavailable_reason': 'model detail has not been resolved',
            })
            detail = _presentation('m1')
            lookup = mock.Mock(return_value=detail)
            response = self._render(
                list_image_models_with_metadata=mock.Mock(return_value=[{
                    'id': 'm1', 'name': 'One', 'description': 'one',
                    'presentation': summary,
                }]),
                model_presentation=lookup,
            )
            self.assertEqual(response.status_code, 200)
            lookup.assert_called_once()
            html = response.content.decode()
            presentation = json.loads(re.search(
                r'<script id="creator-presentation-data" type="application/json">(.*?)</script>',
                html,
            ).group(1))
            operation = presentation['operations']['generate']
            self.assertEqual(operation['selected_model'], 'm1')
            self.assertTrue(operation['availability']['enabled'])
            self.assertIsNone(operation['availability']['reason'])

        def test_nullable_default_remains_null_in_rendered_presentation(self) -> None:
            response = self._render(
                default_model_for_presentation=mock.Mock(return_value=None),
            )
            self.assertEqual(response.status_code, 200)
            html = response.content.decode()
            presentation = json.loads(re.search(
                r'<script id="creator-presentation-data" type="application/json">(.*?)</script>',
                html,
            ).group(1))
            operation = presentation['operations']['generate']
            self.assertIsNone(operation['default_model'])
            self.assertEqual(operation['selected_model'], 'm1')

        def test_summary_resolution_failure_returns_502(self) -> None:
            summary = _presentation('m1')
            summary['detail'] = 'summary'
            response = self._render(
                list_image_models_with_metadata=mock.Mock(return_value=[{
                    'id': 'm1', 'name': 'One', 'description': 'one',
                    'presentation': summary,
                }]),
                model_presentation=mock.Mock(side_effect=RuntimeError('detail failed')),
            )
            self.assertEqual(response.status_code, 502)
            self.assertEqual(
                json.loads(response.content)['error'],
                'could not resolve model presentation: detail failed',
            )

        def test_image_refresh_returns_502_when_edit_discovery_fails(self) -> None:
            request = self.factory.get('/models-json/?provider=venice')
            overrides = {
                'normalize_provider_api': mock.Mock(return_value=('venice', 'generation')),
                'list_image_models_with_metadata': mock.Mock(return_value=[
                    {'id': 'm1', 'name': 'One', 'description': 'one',
                     'presentation': _presentation('m1')},
                ]),
                '_edit_metadata': mock.Mock(
                    side_effect=RuntimeError('catalog unavailable'),
                ),
            }
            with mock.patch.dict(self.view._models_json.__globals__, overrides):
                response = self.view._models_json(request)

            self.assertEqual(response.status_code, 502)
            self.assertEqual(
                json.loads(response.content)['error'],
                'could not list edit models: catalog unavailable',
            )

        def test_image_refresh_separates_discovery_from_selected_target_lookup(self) -> None:
            request = self.factory.get(
                '/models-json/?provider=venice&selected_generate_model=m2'
                '&selected_edit_model=edit-two'
            )
            list_models = mock.Mock(return_value=[
                {'id': 'm1', 'name': 'One', 'description': 'one',
                 'presentation': _presentation('m1')},
                {'id': 'm2', 'name': 'Two', 'description': 'two',
                 'presentation': _presentation('m2')},
            ])
            selected = _presentation('m1')
            selected['controls']['generate']['qualities'] = ['high']
            selected['controls']['generate']['default_quality'] = 'high'
            presentation_lookup = mock.Mock(return_value=selected)
            edit_metadata = mock.Mock(return_value={
                'supports_edit': False,
                'edit_models': [],
                'edit_model_options': [],
                'selected_edit_model': None,
                'default_edit_model': '',
                'edit_aspect_ratios': [],
                'default_edit_aspect_ratio': '',
                'edit_image_sizes': [],
                'default_edit_image_size': '',
            })
            overrides = {
                'normalize_provider_api': mock.Mock(return_value=('venice', 'generation')),
                'list_image_models_with_metadata': list_models,
                'model_presentation': presentation_lookup,
                'aspect_ratios': mock.Mock(return_value=['1:1']),
                'image_sizes': mock.Mock(return_value=['1K']),
                'default_aspect_ratio': mock.Mock(return_value='1:1'),
                'default_image_size': mock.Mock(return_value='1K'),
                'default_model_for_presentation': mock.Mock(return_value='m1'),
                '_provider_config': mock.Mock(return_value={}),
                'supports_edit': mock.Mock(return_value=False),
                'supports_upscale': mock.Mock(return_value=False),
                '_edit_metadata': edit_metadata,
            }
            with mock.patch.dict(self.view._models_json.__globals__, overrides):
                with mock.patch.object(self.view, '_model_tag_states', return_value={}):
                    provider_response = self.view._models_json(request)

                    target_request = self.factory.get(
                        '/models-json/?provider=venice&target=generate&model=m1'
                    )
                    target_response = self.view._models_json(target_request)

            list_models.assert_called_once()
            self.assertEqual(
                edit_metadata.call_args.kwargs['requested_model'], 'edit-two',
            )
            self.assertEqual(presentation_lookup.call_count, 1)
            provider_data = json.loads(provider_response.content)['presentation']
            self.assertEqual(provider_data['operations']['generate']['selected_model'], 'm2')
            self.assertEqual(
                json.loads(target_response.content)['presentation']['target']['model'], 'm1',
            )
            self.assertEqual(
                [call.args[0] for call in presentation_lookup.call_args_list],
                ['m1'],
            )

        def test_model_switch_lookup_failure_preserves_best_effort_200(self) -> None:
            request = self.factory.get(
                '/models-json/?provider=venice&target=generate&model=m1'
            )
            with mock.patch.dict(self.view._models_json.__globals__, {
                'normalize_provider_api': mock.Mock(
                    return_value=('venice', 'generation'),
                ),
                'model_presentation': mock.Mock(
                    side_effect=RuntimeError('detail unavailable'),
                ),
            }):
                response = self.view._models_json(request)
            self.assertEqual(response.status_code, 200)
            envelope = json.loads(response.content)
            self.assertEqual(envelope['presentation']['controls'], {})
            self.assertEqual(envelope['notices'], [])

        def test_api_wide_generation_target_retains_provider_choices(self) -> None:
            presentation = _presentation('m1')
            presentation['controls']['generate'].update({
                'aspect_ratios': [], 'default_aspect_ratio': None,
                'image_sizes': [], 'default_image_size': None,
            })
            with mock.patch.dict(imagegen_view.LLemonImageGenViewSet._image_model_target.__globals__, {
                'model_scoped_parameters': mock.Mock(return_value=False),
                'aspect_ratios': mock.Mock(return_value=['1:1', '16:9']),
                'default_aspect_ratio': mock.Mock(return_value='1:1'),
                'image_sizes': mock.Mock(return_value=['1K', '2K']),
                'default_image_size': mock.Mock(return_value='1K'),
                '_provider_config': mock.Mock(return_value=_PROVIDER_CONFIG),
            }):
                target = self.view._image_model_target(
                    'openrouter', 'chat_completions', 'm1',
                    presentation=presentation,
                )
            self.assertEqual(target['controls']['aspect_ratios'], ['1:1', '16:9'])
            self.assertEqual(target['controls']['image_sizes'], ['1K', '2K'])

        def test_model_scoped_target_preserves_meaningful_empty_choices(self) -> None:
            presentation = _presentation('m1')
            presentation['controls']['generate'].update({
                'aspect_ratios': [], 'default_aspect_ratio': None,
                'image_sizes': [], 'default_image_size': None,
            })
            with mock.patch.dict(imagegen_view.LLemonImageGenViewSet._image_model_target.__globals__, {
                'model_scoped_parameters': mock.Mock(return_value=True),
                '_provider_config': mock.Mock(return_value=_PROVIDER_CONFIG),
            }):
                target = self.view._image_model_target(
                    'segmind', 'inference', 'm1', presentation=presentation,
                )
            self.assertEqual(target['controls']['aspect_ratios'], [])
            self.assertEqual(target['controls']['image_sizes'], [])

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

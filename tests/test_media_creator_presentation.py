"""Tests for the provider-neutral media creator presentation contract."""

import sys
import unittest
from pathlib import Path


LIB = Path(__file__).resolve().parents[1] / 'lib'
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from llemon_djview.media_creator import (  # noqa: E402
    build_creator_presentation,
    build_model_target,
    build_operation_presentation,
)


class MediaCreatorPresentationTests(unittest.TestCase):
    def test_contract_keeps_selected_and_default_model_distinct(self) -> None:
        operation = build_operation_presentation(
            'generate',
            model_options=[{'id': 'model-a', 'display': 'Model A'}],
            selected_model='model-a',
            default_model=None,
            defaults={'size': '1K'},
            controls={'sizes': ['1K']},
            availability={'enabled': True},
        )

        self.assertEqual(operation['selected_model'], 'model-a')
        self.assertIsNone(operation['default_model'])
        self.assertEqual(operation['defaults'], {'size': '1K'})

    def test_contract_supports_independent_and_model_less_operations(self) -> None:
        presentation = build_creator_presentation('provider-a', 'api-a', {
            'generate': build_operation_presentation(
                'generate', selected_model='generate-model',
                default_model='generate-model',
            ),
            'edit': build_operation_presentation(
                'edit', selected_model='edit-model', default_model='edit-model',
            ),
            'upscale': build_operation_presentation(
                'upscale', availability={'enabled': True},
            ),
        })

        self.assertEqual(presentation['provider'], 'provider-a')
        self.assertEqual(
            presentation['operations']['generate']['selected_model'],
            'generate-model',
        )
        self.assertEqual(
            presentation['operations']['edit']['selected_model'],
            'edit-model',
        )
        self.assertIsNone(
            presentation['operations']['upscale']['selected_model'],
        )

    def test_contract_copies_mutable_input_containers(self) -> None:
        options = [{'id': 'model-a', 'capabilities': {'sizes': ['1K']}}]
        defaults = {'duration': 5}
        operation = build_operation_presentation(
            'generate', model_options=options, defaults=defaults,
        )
        options.append({'id': 'model-b'})
        options[0]['capabilities']['sizes'].append('2K')
        defaults['duration'] = 10

        self.assertEqual(operation['model_options'], [{
            'id': 'model-a', 'capabilities': {'sizes': ['1K']},
        }])
        self.assertEqual(operation['defaults'], {'duration': 5})

    def test_model_target_has_complete_cache_identity(self) -> None:
        target = build_model_target(
            'provider-a', 'api-a', 'generate', 'model-a',
            controls={'qualities': ['high']},
        )
        self.assertEqual(target['target'], {
            'provider': 'provider-a',
            'api': 'api-a',
            'operation': 'generate',
            'model': 'model-a',
        })
        self.assertEqual(target['controls']['qualities'], ['high'])

    def test_grove_does_not_import_video_provider_backends_or_parse_model_ids(self) -> None:
        root = Path(__file__).resolve().parents[1]
        view_source = (
            root / 'lib' / 'llemon_djview' / 'videogen.py'
        ).read_text(encoding='utf-8')
        template_source = (
            root / 'lib' / 'llemon_djview' / 'templates' /
            'llemon_video' / 'video.html'
        ).read_text(encoding='utf-8')

        self.assertNotIn('mediagen.videogen.venice', view_source)
        self.assertNotIn("startsWith('kling-')", template_source)
        self.assertNotIn("startsWith('grok-')", template_source)
        self.assertNotIn("includes('upscale')", template_source)

    def test_templates_consume_the_authoritative_presentation_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative, selector in (
            ('llemon_image/image.html', 'selectImageModel'),
            ('llemon_video/video.html', 'selectVideoModel'),
        ):
            source = (
                root / 'lib' / 'llemon_djview' / 'templates' / relative
            ).read_text(encoding='utf-8')
            self.assertIn('creator-presentation-data', source)
            self.assertIn('createMediaRefreshController', source)
            self.assertIn(selector, source)
            self.assertNotIn('model-options-data', source)


if __name__ == '__main__':
    unittest.main()

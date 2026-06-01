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


class DjviewMediaSettingsTests(unittest.TestCase):
    def _fake_django_modules(self, *, render=None, settings_obj=None):
        return {
            'django': types.ModuleType('django'),
            'django.conf': types.SimpleNamespace(
                settings=settings_obj or types.SimpleNamespace(
                    LLEMON_IMAGEGEN_MEDIA_DIR='',
                    LLEMON_IMAGEGEN_LOG_DIR='',
                    LLEMON_VIDEOGEN_MEDIA_DIR='',
                    LLEMON_VIDEOGEN_LOG_DIR='',
                ),
            ),
            'django.http': types.SimpleNamespace(
                FileResponse=object,
                Http404=RuntimeError,
                JsonResponse=object,
                StreamingHttpResponse=object,
            ),
            'django.shortcuts': types.SimpleNamespace(
                redirect=object,
                render=render or (lambda request, template, context: context),
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

    def _import_djview(self):
        fake_django = self._fake_django_modules()
        fake_django['django.http'] = types.SimpleNamespace(
            JsonResponse=object,
            StreamingHttpResponse=object,
        )
        with mock.patch.dict(sys.modules, fake_django):
            import llemon_djview as djview
        return djview

    def test_media_settings_initializes_backends_and_returns_django_values(self) -> None:
        djview = self._import_djview()
        fake_imagegen = types.SimpleNamespace(
            init=mock.Mock(),
            get_media_dir=mock.Mock(return_value='~/img-media'),
            get_log_dir=mock.Mock(return_value='~/img-log'),
        )
        fake_videogen = types.SimpleNamespace(
            init=mock.Mock(),
            get_media_dir=mock.Mock(return_value='~/vid-media'),
            get_log_dir=mock.Mock(return_value=''),
        )
        fake_mediagen = types.SimpleNamespace(
            imagegen=fake_imagegen,
            videogen=fake_videogen,
        )
        fake_appconfig = object()

        with mock.patch.dict(
            sys.modules,
            {
                'hty7.llemon.mediagen': fake_mediagen,
            },
        ):
            settings = djview.media_settings(fake_appconfig)

        fake_imagegen.init.assert_called_once_with(fake_appconfig)
        fake_videogen.init.assert_called_once_with(fake_appconfig)
        self.assertEqual(settings['LLEMON_IMAGEGEN_MEDIA_DIR'], str(Path('~/img-media').expanduser()))
        self.assertEqual(settings['LLEMON_IMAGEGEN_LOG_DIR'], str(Path('~/img-log').expanduser()))
        self.assertIsNone(settings['LLEMON_LOG_DIR'])
        self.assertEqual(settings['LLEMON_VIDEOGEN_MEDIA_DIR'], str(Path('~/vid-media').expanduser()))
        self.assertIsNone(settings['LLEMON_VIDEOGEN_LOG_DIR'])

    def test_media_viewset_combines_image_video_creators_with_shared_pages(self) -> None:
        def fake_reverse(name, args=None, **kwargs):
            route = name.split(':')[-1]
            if args:
                return '/' + route + '/' + '/'.join(str(arg) for arg in args)
            return '/' + route + '/'

        fake_django = self._fake_django_modules()
        fake_django['django.urls'] = types.SimpleNamespace(reverse=fake_reverse)
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.media import LLemonMediaViewSet

        view = LLemonMediaViewSet('llemon_image', 'llemon_media')
        request = types.SimpleNamespace()
        context = view.index(request)

        self.assertEqual(context['title'], 'LLemon Media')
        self.assertEqual(
            [item['name'] for item in context['pages']],
            ['Image Creator', 'Video Creator', 'Gallery', 'Archive', 'Input files'],
        )
        self.assertFalse(hasattr(view, 'video_gallery'))
        self.assertFalse(hasattr(view, 'video_archive'))
        self.assertFalse(hasattr(view, 'video_file'))

    def test_source_dirs_items_include_nick_for_copy_to_gallery(self) -> None:
        fake_django = self._fake_django_modules(render=lambda request, template, context: context)
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.media import LLemonMediaViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            project = gallery / 'project-a'
            project.mkdir(parents=True)
            source = Path(tmp) / 'source'
            source.mkdir()
            (source / 'photo.png').write_bytes(b'png')
            view = LLemonMediaViewSet('llemon_image', 'llemon_media')
            request = types.SimpleNamespace(
                GET={'nick': 'Inputs', 'subdir': '', 'dest_subdir': 'project-a'},
            )

            with mock.patch('llemon_djview.sourcedirs.get_source_dirs', return_value=[{'name': 'Inputs', 'path': str(source)}]):
                with mock.patch.object(view._image, '_media_dir', return_value=tmp):
                    with mock.patch.object(view, '_source_thumb_base', return_value=''):
                        with mock.patch.object(view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                            ctx = view._source_dirs(request)

        self.assertEqual(ctx['title'], 'Source: Inputs -> Gallery / project-a')
        self.assertEqual(ctx['dest_subdir'], 'project-a')
        self.assertEqual(ctx['destination_label'], 'Gallery / project-a')
        self.assertEqual(ctx['images'][0]['nick'], 'Inputs')
        self.assertEqual(ctx['images'][0]['rp'], 'photo.png')
        nav_urls = {item['name']: item['url'] for item in ctx['nav']}
        self.assertEqual(nav_urls['Gallery'], '/gallery/?subdir=project-a')
        self.assertEqual(nav_urls['Input files'], '/source_dirs/?dest_subdir=project-a')

    def test_source_dirs_copy_to_gallery_uses_destination_project(self) -> None:
        class FakeJsonResponse:
            def __init__(self, data, status=200):
                self.data = data
                self.status_code = status

        fake_django = self._fake_django_modules()
        fake_django['django.http'] = types.SimpleNamespace(
            FileResponse=object,
            Http404=RuntimeError,
            JsonResponse=FakeJsonResponse,
            StreamingHttpResponse=object,
        )
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.media import LLemonMediaViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            project = gallery / 'project-a'
            project.mkdir(parents=True)
            source = Path(tmp) / 'source'
            source.mkdir()
            (source / 'photo.png').write_bytes(b'png')
            view = LLemonMediaViewSet('llemon_image', 'llemon_media')
            request = types.SimpleNamespace(
                body=json.dumps({
                    'nick': 'Inputs',
                    'rp': 'photo.png',
                    'dest_subdir': 'project-a',
                }).encode('utf-8'),
            )

            with mock.patch('llemon_djview.sourcedirs.get_source_dirs', return_value=[{'name': 'Inputs', 'path': str(source)}]):
                with mock.patch.object(view._image, '_gallery_dir', return_value=str(gallery)):
                    resp = view._source_dirs_copy_to_gallery(request)

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.data, {'file': 'photo.png', 'subdir': 'project-a'})
            self.assertTrue((project / 'photo.png').is_file())

    def test_mediagen_backends_read_media_dir_from_shared_section(self) -> None:
        from hty7.llemon.mediagen import imagegen, videogen

        class FakeAppConfig:
            def __init__(self, mapping):
                self._mapping = mapping

            def get(self, namespace, section, key):
                return self._mapping.get((namespace, section, key))

        image_before = dict(imagegen._config)
        video_before = dict(videogen._config)
        self.addCleanup(lambda: imagegen._config.clear() or imagegen._config.update(image_before))
        self.addCleanup(lambda: videogen._config.clear() or videogen._config.update(video_before))

        appconfig = FakeAppConfig({
            ('llemon', 'mediagen', 'media_dir'): '~/shared-media',
            ('llemon', 'mediagen', 'log_dir'): '~/shared-log',
            ('llemon', 'mediagen', 'notes_dir'): '~/shared-notes',
            ('llemon', 'mediagen', 'notes_selector'): 'slot-a',
            ('llemon', 'mediagen', 'description_dirs'): [],
            ('llemon', 'mediagen', 'extra_dirs'): [],
        })

        imagegen.init(appconfig)
        videogen.init(appconfig)

        self.assertEqual(imagegen.get_media_dir(), '~/shared-media')
        self.assertEqual(videogen.get_media_dir(), '~/shared-media')
        self.assertEqual(imagegen.get_notes_dir(), str(Path('~/shared-notes').expanduser()))
        self.assertEqual(videogen.get_notes_dir(), str(Path('~/shared-notes').expanduser()))

    def test_mediagen_backends_read_description_dirs_from_shared_section(self) -> None:
        from hty7.llemon.mediagen import imagegen, videogen

        class FakeAppConfig:
            def __init__(self, mapping):
                self._mapping = mapping

            def get(self, namespace, section, key):
                return self._mapping.get((namespace, section, key))

        image_before = dict(imagegen._config)
        video_before = dict(videogen._config)
        self.addCleanup(lambda: imagegen._config.clear() or imagegen._config.update(image_before))
        self.addCleanup(lambda: videogen._config.clear() or videogen._config.update(video_before))

        with tempfile.TemporaryDirectory() as tmp:
            desc_dir = Path(tmp) / 'mediagen'
            desc_dir.mkdir()
            (desc_dir / 'notes.json').write_text(
                '{"reverse-tags":["block"],"tags":["shared-tag"]}',
                encoding='utf-8',
            )
            (desc_dir / 'quirks.json').write_text(
                '{"openrouter":{"openai/gpt-5-image":["needs_image_system_prompt"]}}',
                encoding='utf-8',
            )

            appconfig = FakeAppConfig({
                ('llemon', 'mediagen', 'media_dir'): '~/shared-media',
                ('llemon', 'mediagen', 'description_dirs'): [str(desc_dir)],
                ('llemon', 'mediagen', 'extra_dirs'): [],
                ('llemon', 'mediagen', 'notes_selector'): 'slot-a',
            })

            with mock.patch.dict(sys.modules, {'httpx': types.ModuleType('httpx')}):
                imagegen.init(appconfig)
                videogen.init(appconfig)

        self.assertEqual(imagegen.get_tags(), ['block', 'shared-tag'])
        self.assertEqual(imagegen.get_reverse_tags(), ['block'])
        self.assertEqual(videogen.get_tags(), ['block', 'shared-tag'])
        self.assertEqual(videogen.get_reverse_tags(), ['block'])

    def test_django_settings_loads_llemon_conf_and_initializes_discovery(self) -> None:
        djview = self._import_djview()
        fake_appconfig = object()

        with mock.patch.object(djview, '_AppConfig', return_value=fake_appconfig) as appconfig_cls:
            with mock.patch('os.path.exists', return_value=False):
                with mock.patch.object(djview.discover, 'init') as discover_init:
                    with mock.patch.object(
                        djview,
                        'media_settings',
                        return_value={'LLEMON_IMAGEGEN_MEDIA_DIR': '/tmp/media'},
                    ) as media_settings:
                        settings = djview.django_settings('qat')

        appconfig_cls.assert_called_once_with(str(Path('~/etc/llemon.conf').expanduser()), 'qat')
        discover_init.assert_called_once_with(fake_appconfig)
        media_settings.assert_called_once_with(fake_appconfig)
        self.assertEqual(settings, {'LLEMON_IMAGEGEN_MEDIA_DIR': '/tmp/media'})

    def test_django_settings_overlays_grove_local_djview_conf(self) -> None:
        djview = self._import_djview()
        fake_appconfig = types.SimpleNamespace(
            _data={
                'hty7.llemon.mediagen': {
                    'media_dir': '/base/media',
                    'log_dir': '/base/log',
                },
            },
        )

        with tempfile.NamedTemporaryFile('w', suffix='.conf') as conf:
            conf.write(
                '[hty7.llemon.mediagen]\n'
                'media_dir = "~/overlay/media"\n'
                'input_files = ["Pictures=/srv/pictures"]\n'
            )
            conf.flush()

            with mock.patch.object(djview, '_AppConfig', return_value=fake_appconfig):
                with mock.patch.object(djview, '_DEFAULT_DJVIEW_CONF', conf.name):
                    with mock.patch.object(djview.discover, 'init'):
                        with mock.patch.object(djview, 'media_settings', return_value={}) as media_settings:
                            djview.django_settings('hty7')

        media_settings.assert_called_once_with(fake_appconfig)
        self.assertEqual(
            fake_appconfig._data['hty7.llemon.mediagen'],
            {
                'media_dir': str(Path('~/overlay/media').expanduser()),
                'log_dir': '/base/log',
                'input_files': ['Pictures=/srv/pictures'],
            },
        )

    def test_videogen_empty_upload_matches_image_error_message(self) -> None:
        class FakeJsonResponse:
            def __init__(self, data, status=200):
                self.data = data
                self.status_code = status

        fake_django = self._fake_django_modules()
        fake_django['django.http'] = types.SimpleNamespace(
            FileResponse=object,
            Http404=RuntimeError,
            JsonResponse=FakeJsonResponse,
            StreamingHttpResponse=object,
        )
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.videogen import LLemonVideoGenViewSet

        view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')
        request = types.SimpleNamespace(FILES=types.SimpleNamespace(getlist=lambda key: []), POST={})
        with mock.patch.object(view, '_gallery_dir', return_value='/tmp/gallery'):
            resp = view._upload(request)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data, {'error': 'no files uploaded'})

    def test_image_upload_saves_to_gallery_and_writes_metadata(self) -> None:
        class FakeJsonResponse:
            def __init__(self, data, status=200):
                self.data = data
                self.status_code = status

        fake_django = self._fake_django_modules()
        fake_django['django.http'] = types.SimpleNamespace(
            FileResponse=object,
            Http404=RuntimeError,
            JsonResponse=FakeJsonResponse,
            StreamingHttpResponse=object,
        )
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            gallery.mkdir()
            view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
            request = types.SimpleNamespace(
                FILES=types.SimpleNamespace(
                    getlist=lambda key: [types.SimpleNamespace(name='photo.png')],
                ),
                POST={},
            )

            with mock.patch.object(view, '_gallery_dir', return_value=str(gallery)):
                save_uploaded = mock.Mock(return_value=(['photo.png'], []))
                with mock.patch.dict(
                    view._upload.__globals__,
                    {'save_uploaded_image_files': save_uploaded},
                ):
                    resp = view._upload(request)

            save_uploaded.assert_called_once()
            self.assertEqual(save_uploaded.call_args.args[1], str(gallery))
            self.assertEqual(resp.data, {'files': ['photo.png'], 'errors': []})
            meta = json.loads((gallery / 'photo.json').read_text(encoding='utf-8'))

        self.assertEqual(meta['source'], 'upload')
        self.assertEqual(meta['files'], ['photo.png'])
        self.assertIn('timestamp', meta)
        self.assertEqual(meta['uploaded_at'], meta['timestamp'])

    def test_image_gallery_exposes_upload_url(self) -> None:
        fake_django = self._fake_django_modules(render=lambda request, template, context: context)
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            gallery.mkdir()
            view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
            request = types.SimpleNamespace(GET={}, method='GET')

            with mock.patch.object(view, '_media_dir', return_value=tmp):
                with mock.patch.object(view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                    ctx = view.gallery(request)

        self.assertEqual(ctx['upload_url'], '/upload/')

    def test_project_gallery_nav_links_return_to_project_gallery(self) -> None:
        fake_django = self._fake_django_modules(render=lambda request, template, context: context)
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            project = gallery / 'project-a'
            project.mkdir(parents=True)
            view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
            request = types.SimpleNamespace(GET={'subdir': 'project-a'}, method='GET')

            with mock.patch.object(view, '_media_dir', return_value=tmp):
                with mock.patch.object(view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                    ctx = view.gallery(request)

        nav_urls = {item['name']: item['url'] for item in ctx['nav']}
        self.assertEqual(nav_urls['Image creator'], '/image_creator/?output_subdir=project-a')
        self.assertEqual(nav_urls['Video Creator'], '/video_creator/?output_subdir=project-a')
        self.assertEqual(nav_urls['Gallery'], '/gallery/?subdir=project-a')
        self.assertEqual(nav_urls['Input files'], '/source_dirs/?dest_subdir=project-a')

    def test_image_creator_picker_uses_gallery_images(self) -> None:
        fake_django = self._fake_django_modules()
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            thumbs = gallery / 'thumbnails'
            gallery.mkdir()
            thumbs.mkdir()
            (gallery / 'photo.png').write_bytes(b'png')
            (gallery / 'clip.mp4').write_bytes(b'mp4')
            (thumbs / 'photo.png').write_bytes(b'thumb')

            view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
            with mock.patch.object(view, '_gallery_dir', return_value=str(gallery)):
                with mock.patch.object(view, '_thumb_dir', return_value=str(thumbs)):
                    with mock.patch.object(view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                        items = view._gallery_picker_items()

        self.assertEqual([item['fname'] for item in items], ['photo.png'])
        self.assertEqual(items[0]['url'], '/image_file/photo.png')
        self.assertEqual(items[0]['thumb_url'], '/thumbnail/photo.png')

    def test_image_creator_uses_gallery_edit_and_upscale_endpoints(self) -> None:
        fake_django = self._fake_django_modules(render=lambda request, template, context: context)
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
        request = types.SimpleNamespace(GET={})
        model_row = {
            'id': 'model-a',
            'name': 'Model A',
            'description': '',
        }

        with mock.patch.dict(
            view.image_creator.__globals__,
            {
                'list_image_models_with_metadata': mock.Mock(return_value=[model_row]),
                'get_model_tag_states': mock.Mock(return_value={}),
                'model_quirk_labels': mock.Mock(return_value=[]),
                'default_system_prompt': mock.Mock(return_value=None),
                'get_notes_load_errors': mock.Mock(return_value=[]),
                'get_tags': mock.Mock(return_value=[]),
                'get_reverse_tags': mock.Mock(return_value=[]),
                'get_notes_slot': mock.Mock(return_value=''),
                'aspect_ratios': mock.Mock(return_value=[]),
                'image_sizes': mock.Mock(return_value=[]),
                'default_aspect_ratio': mock.Mock(return_value=''),
                'default_image_size': mock.Mock(return_value=''),
                'default_image_model': mock.Mock(return_value='model-a'),
                '_provider_config': mock.Mock(return_value={}),
            },
        ):
            with mock.patch.object(view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                with mock.patch.object(view, '_gallery_picker_items', return_value=[]):
                    ctx = view.image_creator(request)

        self.assertEqual(ctx['upscale_url'], '/upscale/')
        self.assertEqual(ctx['edit_image_url'], '/edit_image/')

    def test_video_upload_saves_to_gallery_and_writes_metadata(self) -> None:
        class FakeJsonResponse:
            def __init__(self, data, status=200):
                self.data = data
                self.status_code = status

        fake_django = self._fake_django_modules()
        fake_django['django.http'] = types.SimpleNamespace(
            FileResponse=object,
            Http404=RuntimeError,
            JsonResponse=FakeJsonResponse,
            StreamingHttpResponse=object,
        )
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.videogen import LLemonVideoGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            gallery.mkdir()
            view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')
            request = types.SimpleNamespace(
                FILES=types.SimpleNamespace(
                    getlist=lambda key: [types.SimpleNamespace(name='reference.jpg')],
                ),
                POST={},
            )

            with mock.patch.object(view, '_gallery_dir', return_value=str(gallery)):
                save_uploaded = mock.Mock(return_value=(['reference.jpg'], []))
                with mock.patch.dict(
                    view._upload.__globals__,
                    {'save_uploaded_image_files': save_uploaded},
                ):
                    resp = view._upload(request)

            save_uploaded.assert_called_once()
            self.assertEqual(save_uploaded.call_args.args[1], str(gallery))
            self.assertEqual(resp.data, {'files': ['reference.jpg'], 'errors': []})
            meta = json.loads((gallery / 'reference.json').read_text(encoding='utf-8'))

        self.assertEqual(meta['source'], 'upload')
        self.assertEqual(meta['files'], ['reference.jpg'])
        self.assertIn('timestamp', meta)
        self.assertEqual(meta['uploaded_at'], meta['timestamp'])

    def test_video_creator_picker_uses_gallery_images(self) -> None:
        fake_django = self._fake_django_modules()
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.videogen import LLemonVideoGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            gallery.mkdir()
            (gallery / 'reference.jpg').write_bytes(b'jpg')
            (gallery / 'clip.mp4').write_bytes(b'mp4')

            view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')
            with mock.patch.object(view, '_gallery_dir', return_value=str(gallery)):
                with mock.patch.object(view, '_ensure_thumbnail', return_value=True):
                    with mock.patch.object(view, '_ensure_large_thumbnail', return_value=True):
                        with mock.patch.object(view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                            items = view._gallery_picker_items()

        self.assertEqual([item['fname'] for item in items], ['reference.jpg'])
        self.assertEqual(items[0]['url'], '/video_file/reference.jpg')
        self.assertEqual(items[0]['thumb_url'], '/video_thumbnail/reference.jpg')
        self.assertEqual(items[0]['large_thumb_url'], '/video_large_thumbnail/reference.jpg')

    def test_video_creator_project_nav_links_return_to_project_gallery(self) -> None:
        fake_django = self._fake_django_modules(render=lambda request, template, context: context)
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.videogen import LLemonVideoGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            project = gallery / 'project-a'
            project.mkdir(parents=True)
            view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')
            request = types.SimpleNamespace(GET={'output_subdir': 'project-a'})
            model_row = {'id': 'video-model-a', 'display': 'Video Model A'}

            with mock.patch.object(view, '_media_dir', return_value=tmp):
                with mock.patch.object(view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                    with mock.patch.object(view, '_model_options', return_value=[model_row]):
                        with mock.patch.object(view, '_model_tag_states', return_value={}):
                            with mock.patch.object(view, '_gallery_picker_items', return_value=[]):
                                with mock.patch.object(view, '_source_dirs_json_url', return_value='/source_dirs_json/'):
                                    with mock.patch.dict(
                                        view.video_creator.__globals__,
                                        {
                                            'normalize_provider_api': mock.Mock(return_value=('provider-a', 'api-a')),
                                            'get_notes_load_errors': mock.Mock(return_value=[]),
                                            'get_tags': mock.Mock(return_value=[]),
                                            'get_reverse_tags': mock.Mock(return_value=[]),
                                            'get_notes_slot': mock.Mock(return_value=''),
                                            'default_video_model': mock.Mock(return_value='video-model-a'),
                                            'default_duration': mock.Mock(return_value='5'),
                                        },
                                    ):
                                        ctx = view.video_creator(request)

        nav_urls = {item['name']: item['url'] for item in ctx['nav']}
        self.assertEqual(ctx['output_subdir'], 'project-a')
        self.assertEqual(nav_urls['Video creator'], '/video_creator/?output_subdir=project-a')
        self.assertEqual(nav_urls['Gallery'], '/video_gallery/?subdir=project-a')
        self.assertEqual(nav_urls['Image Creator'], '/image_creator/?output_subdir=project-a')
        self.assertEqual(nav_urls['Input files'], '/source_dirs/?dest_subdir=project-a')

    def test_shared_media_root_lists_gallery_and_archive_image_and_video_formats(self) -> None:
        """Unified gallery: gallery and archive list both image and video formats."""
        fake_django = self._fake_django_modules(render=lambda request, template, context: context)
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet
            from llemon_djview.videogen import LLemonVideoGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gallery = root / 'gallery'
            archive = root / 'archive'
            for directory in (gallery, archive):
                directory.mkdir()

            (gallery / 'photo.png').write_bytes(b'png')
            (gallery / 'clip.mp4').write_bytes(b'mp4')
            (archive / 'old.webp').write_bytes(b'webp')
            (archive / 'old.mp4').write_bytes(b'mp4')

            image_view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
            video_view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')
            request = types.SimpleNamespace(GET={}, method='GET')

            with mock.patch.object(image_view, '_media_dir', return_value=tmp):
                with mock.patch.object(image_view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                    with mock.patch.object(image_view, '_ensure_thumbnail', return_value=False):
                        with mock.patch.object(image_view, '_ensure_large_thumbnail', return_value=False):
                            with mock.patch.object(image_view, '_ensure_archive_thumbnail', return_value=False):
                                with mock.patch.object(image_view, '_ensure_archive_large_thumbnail', return_value=False):
                                    gallery_ctx = image_view.gallery(request)
                                    archive_ctx = image_view.archive(request)

            with mock.patch.object(video_view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                video_gallery = video_view._list_videos(
                    str(gallery),
                    'video_file',
                    'video_thumbnail',
                    'video_large_thumbnail',
                )
                video_archive = video_view._list_videos(
                    str(archive),
                    'video_archive_file',
                    'video_archive_thumbnail',
                    'video_archive_large_thumbnail',
                )

        # Both image and video galleries show both types
        self.assertEqual(sorted([item['fname'] for item in gallery_ctx['images']]), ['clip.mp4', 'photo.png'])
        self.assertEqual(sorted([item['fname'] for item in archive_ctx['images']]), ['old.mp4', 'old.webp'])
        self.assertEqual(sorted([item['fname'] for item in video_gallery]), ['clip.mp4', 'photo.png'])
        self.assertEqual(sorted([item['fname'] for item in video_archive]), ['old.mp4', 'old.webp'])

    def test_image_view_can_serve_video_files_in_shared_directory(self) -> None:
        """Unified gallery: image view can serve both image and video files (validation check)."""
        fake_django = self._fake_django_modules()
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        view = LLemonImageGenViewSet('llemon_image', 'llemon_image')

        # _safe_filename should accept video files for serving
        try:
            view._safe_filename('clip.mp4')
            view._safe_filename('video.webm')
        except ValueError:
            self.fail('_safe_filename should accept video files')


class UnifiedGalleryTests(unittest.TestCase):
    """Tests for the unified gallery system where image and video galleries are the same."""

    def _fake_django_modules(self, *, render=None, settings_obj=None):
        return {
            'django': types.ModuleType('django'),
            'django.conf': types.SimpleNamespace(
                settings=settings_obj or types.SimpleNamespace(
                    LLEMON_IMAGEGEN_MEDIA_DIR='',
                    LLEMON_IMAGEGEN_LOG_DIR='',
                    LLEMON_VIDEOGEN_MEDIA_DIR='',
                    LLEMON_VIDEOGEN_LOG_DIR='',
                    LLEMON_GALLERY_DIR='',
                    LLEMON_ARCHIVE_DIR='',
                ),
            ),
            'django.http': types.SimpleNamespace(
                FileResponse=object,
                Http404=RuntimeError,
                JsonResponse=object,
                StreamingHttpResponse=object,
            ),
            'django.shortcuts': types.SimpleNamespace(
                redirect=object,
                render=render or (lambda request, template, context: context),
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

    def test_unified_gallery_image_and_video_views_use_same_directory(self) -> None:
        """Both image and video galleries use the same gallery directory."""
        fake_django = self._fake_django_modules(
            settings_obj=types.SimpleNamespace(
                LLEMON_IMAGEGEN_MEDIA_DIR='/tmp/img',
                LLEMON_IMAGEGEN_LOG_DIR='',
                LLEMON_VIDEOGEN_MEDIA_DIR='/tmp/vid',
                LLEMON_VIDEOGEN_LOG_DIR='',
                LLEMON_GALLERY_DIR='/tmp/shared/gallery',
                LLEMON_ARCHIVE_DIR='/tmp/shared/archive',
            ),
        )
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet
            from llemon_djview.videogen import LLemonVideoGenViewSet

        img_view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
        vid_view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')

        self.assertEqual(img_view._gallery_dir(), '/tmp/shared/gallery')
        self.assertEqual(vid_view._gallery_dir(), '/tmp/shared/gallery')
        self.assertEqual(img_view._archive_dir(), '/tmp/shared/archive')
        self.assertEqual(vid_view._archive_dir(), '/tmp/shared/archive')

    def test_unified_gallery_displays_both_image_and_video_types(self) -> None:
        """Both image and video galleries display both image and video files."""
        fake_django = self._fake_django_modules(render=lambda request, template, context: context)
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet
            from llemon_djview.videogen import LLemonVideoGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            gallery.mkdir()
            (gallery / 'photo.png').write_bytes(b'png')
            (gallery / 'clip.mp4').write_bytes(b'mp4')
            (gallery / 'animation.gif').write_bytes(b'gif')
            (gallery / 'video.webm').write_bytes(b'webm')

            img_view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
            vid_view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')
            request = types.SimpleNamespace(GET={}, method='GET')

            with mock.patch.object(img_view, '_media_dir', return_value=tmp):
                with mock.patch.object(img_view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                    with mock.patch.object(img_view, '_ensure_thumbnail', return_value=False):
                        with mock.patch.object(img_view, '_ensure_large_thumbnail', return_value=False):
                            img_gallery = img_view.gallery(request)

            with mock.patch.object(vid_view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                vid_gallery = vid_view._list_videos(
                    str(gallery),
                    'video_file',
                    'video_thumbnail',
                    'video_large_thumbnail',
                )

            img_files = sorted([item['fname'] for item in img_gallery['images']])
            vid_files = sorted([item['fname'] for item in vid_gallery])

            # Image view should see all 4 files
            self.assertEqual(img_files, ['animation.gif', 'clip.mp4', 'photo.png', 'video.webm'])
            # Video view should see all 4 files
            self.assertEqual(vid_files, ['animation.gif', 'clip.mp4', 'photo.png', 'video.webm'])

    def test_upload_validation_allows_images_only(self) -> None:
        """Upload endpoints accept images but reject videos."""
        fake_django = self._fake_django_modules()
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        view = LLemonImageGenViewSet('llemon_image', 'llemon_image')

        # Images should be accepted
        try:
            view._safe_image_name('photo.png')
            view._safe_image_name('image.jpg')
            view._safe_image_name('animation.gif')
        except ValueError:
            self.fail('Image extensions should be accepted by _safe_image_name')

        # Videos should be rejected
        with self.assertRaises(ValueError):
            view._safe_image_name('video.mp4')
        with self.assertRaises(ValueError):
            view._safe_image_name('clip.webm')

    def test_file_serving_accepts_both_types(self) -> None:
        """File serving (_safe_filename) accepts both images and videos."""
        fake_django = self._fake_django_modules()
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        view = LLemonImageGenViewSet('llemon_image', 'llemon_image')

        # Both images and videos should be accepted by _safe_filename
        try:
            view._safe_filename('photo.png')
            view._safe_filename('video.mp4')
            view._safe_filename('clip.webm')
            view._safe_filename('animation.gif')
        except ValueError:
            self.fail('Both image and video extensions should be accepted by _safe_filename')

    def test_image_gallery_can_serve_both_types(self) -> None:
        """Image gallery can serve video files (validation check only)."""
        fake_django = self._fake_django_modules()
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet

        view = LLemonImageGenViewSet('llemon_image', 'llemon_image')

        # _safe_filename should accept both image and video files
        try:
            view._safe_filename('photo.png')
            view._safe_filename('clip.mp4')
        except ValueError:
            self.fail('_safe_filename should accept both image and video files')

    def test_video_gallery_can_serve_both_types(self) -> None:
        """Video gallery can serve image files (validation check only)."""
        fake_django = self._fake_django_modules()
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.videogen import LLemonVideoGenViewSet

        view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')

        # _safe_name should accept both image and video files for file serving
        try:
            view._safe_name('photo.png')
            view._safe_name('clip.mp4')
        except ValueError:
            self.fail('_safe_name should accept both image and video files')

    def test_categories_shared_across_galleries(self) -> None:
        """Image and video galleries share the same category database."""
        fake_django = self._fake_django_modules()
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet
            from llemon_djview.videogen import LLemonVideoGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            gallery = Path(tmp) / 'gallery'
            gallery.mkdir()

            img_view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
            vid_view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')

            with mock.patch.object(img_view, '_media_dir', return_value=tmp):
                with mock.patch.object(vid_view, '_media_dir', return_value=tmp):
                    img_cats = img_view._gallery_category_db()
                    vid_cats = vid_view._gallery_category_db()

                    # Create a category in image view
                    img_cats.create('test-category')
                    img_rows = img_cats.rows()

                    # Should be visible in video view
                    vid_rows = vid_cats.rows()
                    self.assertEqual(len(img_rows), 1)
                    self.assertEqual(len(vid_rows), 1)
                    self.assertEqual(img_rows[0]['name'], vid_rows[0]['name'])

    def test_invalid_filenames_rejected_by_both_views(self) -> None:
        """Both views reject hidden files and empty names."""
        fake_django = self._fake_django_modules()
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet
            from llemon_djview.videogen import LLemonVideoGenViewSet

        img_view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
        vid_view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')

        # Hidden files should be rejected by image view
        for name in ['.hidden.mp4', '.config', '.env.png']:
            with self.assertRaises(ValueError):
                img_view._safe_filename(name)

        # Hidden files should be rejected by video view
        for name in ['.hidden.png', '.config.mp4', '.env']:
            with self.assertRaises(ValueError):
                vid_view._safe_name(name)

        # Empty names should be rejected
        with self.assertRaises(ValueError):
            img_view._safe_filename('')
        with self.assertRaises(ValueError):
            vid_view._safe_name('')


if __name__ == '__main__':
    unittest.main()

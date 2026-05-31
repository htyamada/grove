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
            ['Image Creator', 'Video Creator', 'Gallery', 'Uploads', 'Archive', 'Source Dirs'],
        )
        self.assertFalse(hasattr(view, 'video_gallery'))
        self.assertFalse(hasattr(view, 'video_uploads'))
        self.assertFalse(hasattr(view, 'video_archive'))
        self.assertFalse(hasattr(view, 'video_file'))

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

    def test_videogen_upload_list_uses_thumbnail_routes(self) -> None:
        fake_django = self._fake_django_modules()
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.videogen import LLemonVideoGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            uploads_dir = Path(tmp) / 'uploads'
            uploads_dir.mkdir()
            (uploads_dir / 'sample.png').write_bytes(b'png')

            view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')
            with mock.patch.object(view, '_media_dir', return_value=tmp):
                with mock.patch.object(view, '_ensure_uploads_thumbnail', return_value=True):
                    with mock.patch.object(view, '_ensure_uploads_large_thumbnail', return_value=True):
                        with mock.patch.object(
                            view,
                            '_u',
                            side_effect=lambda name, *args: f'/{name}/' + '/'.join(args),
                        ):
                            request = types.SimpleNamespace(
                                build_absolute_uri=lambda url: f'http://testserver{url}',
                            )
                            items = view._list_uploads(request)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['url'], '/video_uploads_image_file/sample.png')
        self.assertEqual(items[0]['thumb_url'], '/video_uploads_thumbnail/sample.png')
        self.assertEqual(items[0]['large_thumb_url'], '/video_uploads_large_thumbnail/sample.png')

    def test_videogen_empty_uploads_match_image_error_message(self) -> None:
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
        request = types.SimpleNamespace(FILES=types.SimpleNamespace(getlist=lambda key: []))
        with mock.patch.object(view, '_uploads_dir', return_value='/tmp/uploads'):
            resp = view._upload_uploads(request)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data, {'error': 'no files uploaded'})

    def test_shared_media_root_lists_both_image_and_video_formats(self) -> None:
        """Unified gallery: all directories list both image and video formats."""
        fake_django = self._fake_django_modules(render=lambda request, template, context: context)
        with mock.patch.dict(sys.modules, fake_django):
            from llemon_djview.imagegen import LLemonImageGenViewSet
            from llemon_djview.videogen import LLemonVideoGenViewSet

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gallery = root / 'gallery'
            archive = root / 'archive'
            uploads = root / 'uploads'
            for directory in (gallery, archive, uploads):
                directory.mkdir()

            (gallery / 'photo.png').write_bytes(b'png')
            (gallery / 'clip.mp4').write_bytes(b'mp4')
            (archive / 'old.webp').write_bytes(b'webp')
            (archive / 'old.mp4').write_bytes(b'mp4')
            (uploads / 'start.png').write_bytes(b'png')
            (uploads / 'ref.jpg').write_bytes(b'jpg')

            image_view = LLemonImageGenViewSet('llemon_image', 'llemon_image')
            video_view = LLemonVideoGenViewSet('llemon_video', 'llemon_video')
            request = types.SimpleNamespace(GET={}, method='GET')

            with mock.patch.object(image_view, '_media_dir', return_value=tmp):
                with mock.patch.object(image_view, '_u', side_effect=lambda name, *args: f'/{name}/' + '/'.join(args)):
                    with mock.patch.object(image_view, '_ensure_thumbnail', return_value=False):
                        with mock.patch.object(image_view, '_ensure_large_thumbnail', return_value=False):
                            with mock.patch.object(image_view, '_ensure_archive_thumbnail', return_value=False):
                                with mock.patch.object(image_view, '_ensure_archive_large_thumbnail', return_value=False):
                                    with mock.patch.object(image_view, '_ensure_uploads_thumbnail', return_value=False):
                                        with mock.patch.object(image_view, '_ensure_uploads_large_thumbnail', return_value=False):
                                            gallery_ctx = image_view.gallery(request)
                                            archive_ctx = image_view.archive(request)
                                            uploads_ctx = image_view.uploads(request)

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
                with mock.patch.object(video_view, '_media_dir', return_value=tmp):
                    with mock.patch.object(video_view, '_ensure_uploads_thumbnail', return_value=False):
                        with mock.patch.object(video_view, '_ensure_uploads_large_thumbnail', return_value=False):
                            video_uploads = video_view._list_uploads()

        # Both image and video galleries show both types
        self.assertEqual(sorted([item['fname'] for item in gallery_ctx['images']]), ['clip.mp4', 'photo.png'])
        self.assertEqual(sorted([item['fname'] for item in archive_ctx['images']]), ['old.mp4', 'old.webp'])
        self.assertEqual(sorted([item['fname'] for item in uploads_ctx['images']]), ['ref.jpg', 'start.png'])
        self.assertEqual(sorted([item['fname'] for item in video_gallery]), ['clip.mp4', 'photo.png'])
        self.assertEqual(sorted([item['fname'] for item in video_archive]), ['old.mp4', 'old.webp'])
        self.assertEqual(sorted([item['fname'] for item in video_uploads]), ['ref.jpg', 'start.png'])

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
                    LLEMON_UPLOADS_DIR='',
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
                LLEMON_UPLOADS_DIR='/tmp/shared/uploads',
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
        self.assertEqual(img_view._uploads_dir(), '/tmp/shared/uploads')
        self.assertEqual(vid_view._uploads_dir(), '/tmp/shared/uploads')

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

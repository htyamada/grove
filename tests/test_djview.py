import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))

try:
    from django.conf import settings
except ModuleNotFoundError:
    settings = None


if settings is None:
    class ImhandlerDjviewSemanticSearchTests(unittest.TestCase):
        @unittest.skip('django is not installed')
        def test_django_required(self) -> None:
            pass
else:
    _template_root = Path(tempfile.mkdtemp(prefix='imhandler-djview-templates-'))
    (_template_root / 'base').mkdir(parents=True, exist_ok=True)
    (_template_root / 'base' / 'base.html').write_text(
        '{% block content %}{% endblock %}',
        encoding='utf-8',
    )

    if not settings.configured:
        settings.configure(
            SECRET_KEY='test-secret',
            ROOT_URLCONF=__name__,
            ALLOWED_HOSTS=['*'],
            INSTALLED_APPS=[
                'django.contrib.contenttypes',
                'django.contrib.sessions',
                'imhandler.djview',
            ],
            TEMPLATES=[
                {
                    'BACKEND': 'django.template.backends.django.DjangoTemplates',
                    'DIRS': [str(_template_root)],
                    'APP_DIRS': True,
                }
            ],
            MIDDLEWARE=[],
        )

    import django

    django.setup()

    from django.test import RequestFactory
    from django.urls import include, path
    from django.contrib.sessions.middleware import SessionMiddleware

    from imhandler import appconfig
    from imhandler.djview import ImageHandlerViewSet
    from imhandler.models import Album

    _vs = ImageHandlerViewSet(base_nav=[], nav_rel=[])
    app_name = 'image_handler'
    _image_handler_patterns = ([
        path('', _vs.index, name='index'),
        path('browse/', _vs.browse, name='browse'),
        path('similarity/', _vs.similarity_browse, name='similarity_browse'),
        path('semantic/', _vs.semantic_search, name='semantic_search'),
        path('compare/', _vs.compare, name='compare'),
        path('cluster/<int:cluster_id>/', _vs.cluster_detail, name='cluster_detail'),
        path('embed-stream/', _vs.embed_stream, name='embed_stream'),
        path('embed-cancel/', _vs.embed_cancel, name='embed_cancel'),
        path('mark/', _vs.mark_toggle, name='mark_toggle'),
        path('deletion-list/', _vs.deletion_list_download, name='deletion_list_download'),
        path('deletion-list/clear/', _vs.deletion_list_clear, name='deletion_list_clear'),
        path('similar/', _vs.similar, name='similar'),
        path('thumb/', _vs.thumb, name='thumb'),
        path('image/', _vs.image, name='image'),
    ], app_name)
    urlpatterns = [
        path('', include(_image_handler_patterns, namespace='image_handler')),
    ]

    class ImhandlerDjviewSemanticSearchTests(unittest.TestCase):
        def setUp(self) -> None:
            self.tmp = tempfile.TemporaryDirectory()
            self.addCleanup(self.tmp.cleanup)
            self.root = Path(self.tmp.name) / 'images'
            self.root.mkdir()
            self.cache = Path(self.tmp.name) / 'cache'
            appconfig.image_roots = [str(self.root)]
            appconfig.image_root_names = ['Images']
            appconfig.cache_dir = str(self.cache)
            self.factory = RequestFactory()

        def _with_session(self, request):
            middleware = SessionMiddleware(lambda req: None)
            middleware.process_request(request)
            return request

        def test_semantic_search_renders_top_level_results(self) -> None:
            album = self.root / 'album1'
            album.mkdir()
            image_path = album / 'cat.jpg'
            image_path.write_bytes(b'not-a-real-image')

            request = self.factory.get('/semantic/', {
                'q': 'cat on a chair',
                'n': '17',
            })

            fake_results = [{
                'path': str(image_path),
                'similarity': 0.987,
                'width': 640,
                'height': 480,
            }]

            with mock.patch('imhandler.db.open_db') as open_db_mock:
                conn = open_db_mock.return_value
                with mock.patch('imhandler.embedder.find_semantic',
                                return_value=(fake_results, 1)) as find_semantic_mock:
                    response = _vs.semantic_search(request)

            self.assertEqual(response.status_code, 200)
            html = response.content.decode('utf-8')
            self.assertIn('cat.jpg', html)
            self.assertIn('0.987', html)
            self.assertIn('First 1 result', html)
            self.assertIn('name="n"', html)
            self.assertIn('image/?path=', html)
            find_semantic_mock.assert_called_once_with(
                conn, 'cat on a chair', n=17
            )
            conn.close.assert_called_once_with()

        def test_index_hides_semantic_link_when_route_missing(self) -> None:
            request = self.factory.get('/')
            real_reverse = __import__('django.urls', fromlist=['reverse']).reverse

            def fake_reverse(viewname, *args, **kwargs):
                if viewname == 'image_handler:semantic_search':
                    from django.urls import NoReverseMatch
                    raise NoReverseMatch('missing semantic route')
                return real_reverse(viewname, *args, **kwargs)

            with mock.patch('imhandler.djview.reverse', side_effect=fake_reverse):
                response = _vs.index(request)

            self.assertEqual(response.status_code, 200)
            html = response.content.decode('utf-8')
            self.assertIn('Browse', html)
            self.assertNotIn('Semantic</a>', html)

        def test_similarity_browse_uses_resolved_album_for_embed_url(self) -> None:
            album = Album(path=self.root, rel_path=Path('.'), name='images', depth=0, images=[])
            request = self.factory.get('/similarity/', {'album': 'missing'})

            with mock.patch('imhandler.djview.scan_all', return_value=album):
                response = _vs.similarity_browse(request)

            self.assertEqual(response.status_code, 200)
            html = response.content.decode('utf-8')
            self.assertIn('/embed\\u002Dstream/?album\\u003D.', html)

        def test_similarity_browse_shows_embed_for_multi_root_virtual_album(self) -> None:
            other_root = Path(self.tmp.name) / 'other-images'
            other_root.mkdir()
            appconfig.image_roots = [str(self.root), str(other_root)]
            appconfig.image_root_names = ['Images', 'Other']
            virtual = Album(path=self.root, rel_path=Path('.'), name='Images', depth=0, images=[])
            request = self.factory.get('/similarity/', {'album': '.'})

            with mock.patch('imhandler.djview.scan_all', return_value=virtual):
                response = _vs.similarity_browse(request)

            self.assertEqual(response.status_code, 200)
            html = response.content.decode('utf-8')
            self.assertIn('id="embed-btn"', html)
            self.assertIn('/embed\\u002Dstream/?album\\u003D.', html)

        def test_embed_stream_virtual_root_runs_all_real_roots(self) -> None:
            other_root = Path(self.tmp.name) / 'other-images'
            other_root.mkdir()
            appconfig.image_roots = [str(self.root), str(other_root)]
            appconfig.image_root_names = ['Images', 'Other']
            request = self._with_session(self.factory.get('/embed-stream/', {'album': '.'}))

            calls = []

            def fake_embed_images(target, conn, *, cancel=None, on_progress=None):
                calls.append(Path(target))
                if on_progress is not None:
                    on_progress(100, Path(target).name)
                return (1, 0)

            conn = mock.Mock()
            with mock.patch('imhandler.db.open_db', return_value=conn):
                with mock.patch('imhandler.embedder.embed_images', side_effect=fake_embed_images):
                    response = _vs.embed_stream(request)
                    body = b''.join(response.streaming_content).decode('utf-8')

            self.assertEqual(calls, [self.root.resolve(), other_root.resolve()])
            self.assertIn('Embedding 2 roots', body)
            self.assertIn('"processed": 2', body)


if __name__ == '__main__':
    unittest.main()

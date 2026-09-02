from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from .. import documents
from . import fixtures
from .base import DocumentViewTestCase

User = get_user_model()


class DocumentViewClientTestCase(DocumentViewTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username='dv-test-user')
        self.client = Client()
        self.client.force_login(self.user)

    def get(self, url, **kwargs):
        return self.client.get(url, HTTP_HOST='localhost', **kwargs)

    def post(self, url, data=None, **kwargs):
        return self.client.post(url, data or {}, HTTP_HOST='localhost', **kwargs)


class DownloadTests(DocumentViewClientTestCase):
    def test_download_exact_bytes_and_filename(self):
        self.touch('a/Notes.txt', b'exact original bytes')
        r = self.get('/documents/download/a/Notes.txt/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Notes.txt', r['Content-Disposition'])
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertEqual(b''.join(r.streaming_content), b'exact original bytes')

    def test_download_each_variant_independently(self):
        self.touch('a/Book.epub', b'epub-bytes')
        self.touch('a/Book.pdf', b'pdf-bytes')
        r1 = self.get('/documents/download/a/Book.epub/')
        r2 = self.get('/documents/download/a/Book.pdf/')
        self.assertEqual(b''.join(r1.streaming_content), b'epub-bytes')
        self.assertEqual(b''.join(r2.streaming_content), b'pdf-bytes')

    def test_download_missing_file_404(self):
        r = self.get('/documents/download/a/nope.txt/')
        self.assertEqual(r.status_code, 404)

    def test_download_traversal_rejected(self):
        r = self.get('/documents/download/../etc/passwd/')
        self.assertIn(r.status_code, (404, 400))

    def test_download_requires_authorization(self):
        anon = Client()
        self.touch('a/Notes.txt', b'x')
        r = anon.get('/documents/download/a/Notes.txt/', HTTP_HOST='localhost')
        self.assertEqual(r.status_code, 403)


class PreviewSubresourceHttpTests(DocumentViewClientTestCase):
    def test_pdf_page_subresource(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        r = self.get('/documents/preview/a/Doc.pdf/?kind=pdf-page&page=1')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/jpeg')

    def test_pdf_page_missing_param_400(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        r = self.get('/documents/preview/a/Doc.pdf/?kind=pdf-page')
        self.assertEqual(r.status_code, 400)

    def test_cbz_page_subresource(self):
        self.mkdir('a')
        fixtures.make_cbz(self.root / 'a' / 'Comic.cbz')
        r = self.get('/documents/preview/a/Comic.cbz/?kind=cbz-page&page=1')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/jpeg')

    def test_epub_image_subresource_bad_id_400(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        r = self.get('/documents/preview/a/Book.epub/?kind=epub-image&id=garbage')
        self.assertEqual(r.status_code, 400)

    def test_unsupported_kind_404(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        r = self.get('/documents/preview/a/Doc.pdf/?kind=cbz-page&page=1')
        self.assertEqual(r.status_code, 404)


class ViewPageIntegrationTests(DocumentViewClientTestCase):
    def test_view_page_embeds_pdf_preview_images(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        r = self.get('/documents/view/a/Doc.pdf/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'kind=pdf-page', r.content)

    def test_view_page_embeds_markdown_html(self):
        self.touch('a/Notes.md', b'# Hello\n\nWorld.')
        r = self.get('/documents/view/a/Notes.md/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'World', r.content)

    def test_view_page_has_download_link_per_variant(self):
        self.touch('a/Book.epub', b'e')
        self.touch('a/Book.pdf', b'p')
        r = self.get('/documents/view/a/Book.epub/')
        self.assertIn(b'/documents/download/a/Book.epub/', r.content)
        self.assertIn(b'/documents/download/a/Book.pdf/', r.content)

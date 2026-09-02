"""Security-response-header coverage (spec 1.6).

The CSP was specified, documented, and then not actually implemented --
these tests exist so that gap cannot reopen silently.
"""
from . import fixtures
from .test_download import DocumentViewClientTestCase


class ResourceHeaderTests(DocumentViewClientTestCase):
    def _assert_resource_headers(self, response):
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        csp = response['Content-Security-Policy']
        self.assertIn("default-src 'none'", csp)
        self.assertIn('sandbox', csp)

    def test_cover_response(self):
        self.touch('a/Notes.txt', b'hi')
        self._assert_resource_headers(self.get('/documents/cover/a/Notes.txt/?size=thumb'))

    def test_download_response(self):
        self.touch('a/Notes.txt', b'hi')
        self._assert_resource_headers(self.get('/documents/download/a/Notes.txt/'))

    def test_pdf_preview_subresource_response(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        self._assert_resource_headers(self.get('/documents/preview/a/Doc.pdf/?kind=pdf-page&page=1'))

    def test_cbz_preview_subresource_response(self):
        self.mkdir('a')
        fixtures.make_cbz(self.root / 'a' / 'Comic.cbz')
        self._assert_resource_headers(self.get('/documents/preview/a/Comic.cbz/?kind=cbz-page&page=1'))


class HtmlPageHeaderTests(DocumentViewClientTestCase):
    def _csp(self, response):
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        return response['Content-Security-Policy']

    def test_browse_page_forbids_script_and_remote_images(self):
        self.touch('a/Book.epub')
        csp = self._csp(self.get('/documents/browse/a/'))
        self.assertIn("script-src 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("frame-src 'none'", csp)
        self.assertIn("base-uri 'none'", csp)
        self.assertIn("img-src 'self';", csp)
        self.assertNotIn('https:', csp)

    def test_epub_detail_page_allows_only_self_images(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        csp = self._csp(self.get('/documents/view/a/Book.epub/'))
        self.assertIn("img-src 'self';", csp)
        self.assertNotIn('https:', csp)

    def test_cbz_detail_page_allows_only_self_images(self):
        self.mkdir('a')
        fixtures.make_cbz(self.root / 'a' / 'Comic.cbz')
        csp = self._csp(self.get('/documents/view/a/Comic.cbz/'))
        self.assertIn("img-src 'self';", csp)
        self.assertNotIn('https:', csp)

    def test_markdown_detail_page_additionally_allows_remote_images(self):
        # A locally-authored Markdown file may legitimately embed a remote
        # diagram (spec 1.3/4.4), so this one page type widens img-src --
        # and only img-src.
        self.touch('a/Notes.md', b'![x](https://example.com/x.png)')
        csp = self._csp(self.get('/documents/view/a/Notes.md/'))
        self.assertIn("img-src 'self' https: http:", csp)
        self.assertIn("script-src 'none'", csp)

    def test_text_detail_page_does_not_widen_img_src(self):
        self.touch('a/Notes.txt', b'plain')
        csp = self._csp(self.get('/documents/view/a/Notes.txt/'))
        self.assertIn("img-src 'self';", csp)
        self.assertNotIn('https:', csp)

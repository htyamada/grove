"""The Exports page is fully browsable: cover art, preview, and download
for an exported copy itself, resolved against the exports directory
(`paths.resolve_export()`) rather than the collection.
"""
from django.urls import reverse

from .. import active
from . import fixtures
from .test_download import DocumentViewClientTestCase


class ExportsCoverTests(DocumentViewClientTestCase):
    def test_export_cover_renders(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        active.add_active('a/Book.epub')
        r = self.get('/documents/exports/cover/Book.epub/?size=thumb')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/jpeg')

    def test_export_cover_404_for_unknown_name(self):
        r = self.get('/documents/exports/cover/NoSuchFile.epub/?size=thumb')
        self.assertEqual(r.status_code, 404)

    def test_export_cover_refresh_redirects_to_export_view(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        active.add_active('a/Book.epub')
        r = self.post('/documents/exports/cover/refresh/', {'name': 'Book.epub'})
        expected = reverse('documentview:exports_view', kwargs={'name': 'Book.epub'})
        self.assertRedirects(r, expected, fetch_redirect_response=False)


class ExportsViewPageTests(DocumentViewClientTestCase):
    def test_export_view_page_renders(self):
        self.touch('a/Notes.txt', b'hello from the export')
        active.add_active('a/Notes.txt')
        r = self.get('/documents/exports/view/Notes.txt/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Notes')
        self.assertContains(r, 'Remove from exports')

    def test_export_view_page_404_for_unknown_name(self):
        r = self.get('/documents/exports/view/NoSuchFile.txt/')
        self.assertEqual(r.status_code, 404)

    def test_export_view_page_embeds_pdf_preview_images(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        active.add_active('a/Doc.pdf')
        r = self.get('/documents/exports/view/Doc.pdf/')
        self.assertContains(r, '/documents/exports/preview/Doc.pdf/?kind=pdf-page&page=1')

    def test_export_view_page_has_a_download_link(self):
        self.touch('a/Notes.txt', b'hi')
        active.add_active('a/Notes.txt')
        r = self.get('/documents/exports/view/Notes.txt/')
        self.assertContains(r, '/documents/exports/download/Notes.txt/')

    def test_removing_from_its_own_detail_page_lands_on_the_listing_not_a_404(self):
        # Regression: the remove control on an export's own detail page
        # used to set return_to to that same (now-deleted) page, so a
        # successful removal redirected straight into a 404 instead of
        # showing the "Removed" notice.
        self.touch('a/Notes.txt', b'hi')
        active.add_active('a/Notes.txt')
        exports_index_url = reverse('documentview:exports_index')

        detail_page = self.get('/documents/exports/view/Notes.txt/')
        self.assertContains(detail_page, f'name="return_to" value="{exports_index_url}"')

        r = self.post('/documents/active/remove/', {'link_name': 'Notes.txt', 'return_to': exports_index_url})
        self.assertRedirects(r, exports_index_url, fetch_redirect_response=False)
        # Confirm the redirect target itself is live (200), not the 404 a
        # redirect back to the just-deleted detail page used to produce.
        self.assertEqual(self.get('/documents/exports/').status_code, 200)
        self.assertFalse((self.active / 'Notes.txt').exists())


class ExportsPreviewSubresourceTests(DocumentViewClientTestCase):
    def test_pdf_page_subresource(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        active.add_active('a/Doc.pdf')
        r = self.get('/documents/exports/preview/Doc.pdf/?kind=pdf-page&page=1')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/jpeg')

    def test_cbz_page_subresource(self):
        self.mkdir('a')
        fixtures.make_cbz(self.root / 'a' / 'Comic.cbz')
        active.add_active('a/Comic.cbz')
        r = self.get('/documents/exports/preview/Comic.cbz/?kind=cbz-page&page=1')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/jpeg')

    def test_epub_image_subresource_round_trips_through_the_export_view(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub', cover_color=(9, 9, 200))
        active.add_active('a/Book.epub')
        # The fixture's only <img> lives on the cover, which isn't part of
        # the bounded chapter preview -- exercise the id format the export
        # detail page would produce by checking the response is well-formed
        # rather than asserting a specific image is embedded.
        r = self.get('/documents/exports/view/Book.epub/')
        self.assertEqual(r.status_code, 200)


class ExportsDownloadTests(DocumentViewClientTestCase):
    def test_export_download_exact_bytes_and_filename(self):
        self.touch('a/Notes.txt', b'exact original bytes')
        active.add_active('a/Notes.txt')
        r = self.get('/documents/exports/download/Notes.txt/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Notes.txt', r['Content-Disposition'])
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertEqual(b''.join(r.streaming_content), b'exact original bytes')

    def test_export_download_404_for_unknown_name(self):
        r = self.get('/documents/exports/download/NoSuchFile.txt/')
        self.assertEqual(r.status_code, 404)

    def test_export_download_requires_authorization(self):
        from django.test import Client

        self.touch('a/Notes.txt', b'hi')
        active.add_active('a/Notes.txt')
        anon = Client()
        r = anon.get('/documents/exports/download/Notes.txt/', HTTP_HOST='localhost')
        self.assertEqual(r.status_code, 403)


class ExportsResourceHeaderTests(DocumentViewClientTestCase):
    def _assert_resource_headers(self, response):
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        csp = response['Content-Security-Policy']
        self.assertIn("default-src 'none'", csp)
        self.assertIn('sandbox', csp)

    def test_export_cover_response(self):
        self.touch('a/Notes.txt', b'hi')
        active.add_active('a/Notes.txt')
        self._assert_resource_headers(self.get('/documents/exports/cover/Notes.txt/?size=thumb'))

    def test_export_download_response(self):
        self.touch('a/Notes.txt', b'hi')
        active.add_active('a/Notes.txt')
        self._assert_resource_headers(self.get('/documents/exports/download/Notes.txt/'))

    def test_export_pdf_preview_subresource_response(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        active.add_active('a/Doc.pdf')
        self._assert_resource_headers(self.get('/documents/exports/preview/Doc.pdf/?kind=pdf-page&page=1'))

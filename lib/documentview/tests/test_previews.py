import io
from unittest import mock

from django.core import signing
from django.test import override_settings

from .. import documents, paths, pdfrender, previews, subresources
from . import fixtures
from .base import DocumentViewTestCase


def _scan(root, rel):
    abs_dir = root / rel if rel else root
    return documents.scan_directory(abs_dir, rel)


class EpubPreviewTests(DocumentViewTestCase):
    def test_toc_preface_and_first_chapter_present(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            result = previews.epub_preview(resolved, resolved.rel_path)
        self.assertTrue(result['sections'])
        self.assertIn('chapter1.xhtml', result['sections'][0]['label'])

    def test_epub_no_cover_still_has_chapter_section(self):
        self.mkdir('a')
        fixtures.make_epub_no_cover(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            result = previews.epub_preview(resolved, resolved.rel_path)
        self.assertEqual(len(result['sections']), 1)

    def test_front_matter_does_not_use_every_slot_before_first_chapter(self):
        self.mkdir('a')
        fixtures.make_epub_with_front_matter(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            result = previews.epub_preview(resolved, resolved.rel_path)
        labels = [section['label'] for section in result['sections']]
        self.assertEqual(len(labels), 3)
        self.assertIn('chapter1.xhtml', labels)

    def test_malformed_epub_degrades_gracefully(self):
        self.mkdir('a')
        fixtures.make_malformed_zip(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            result = previews.epub_preview(resolved, resolved.rel_path)
        self.assertEqual(result, {'toc': [], 'sections': []})

    def test_section_count_capped_by_setting(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        with override_settings(DOCUMENT_VIEWER_MAX_PREVIEW_SECTIONS=0):
            with paths.resolve_document('a/Book.epub') as resolved:
                result = previews.epub_preview(resolved, resolved.rel_path)
        self.assertEqual(result['sections'], [])

    def test_section_bytes_capped_by_setting(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        with override_settings(DOCUMENT_VIEWER_MAX_PREVIEW_BYTES=5):
            with paths.resolve_document('a/Book.epub') as resolved:
                result = previews.epub_preview(resolved, resolved.rel_path)
        # Still produces a (truncated, sanitized) section rather than failing.
        self.assertTrue(result['sections'])

    def test_img_rewritten_to_signed_preview_subresource(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            result = previews.epub_preview(resolved, resolved.rel_path)
        # The fixture's chapter has no <img>, so nothing to rewrite there;
        # exercise the rewrite path directly against a manifest image.
        with paths.resolve_document('a/Book.epub') as resolved:
            html_out = previews._rewrite_epub_html(
                '<img src="cover.jpg">',
                {
                    'cover-img': {'href': 'cover.jpg', 'media_type': 'image/jpeg', 'properties': ['cover-image']},
                },
                ['cover-img'],
                '',
                '',
                resolved,
                resolved.rel_path,
            )
        self.assertIn('kind=epub-image', html_out)
        self.assertIn('id=', html_out)

    def test_remote_image_dropped(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            html_out = previews._rewrite_epub_html(
                '<img src="https://example.com/x.jpg">',
                {},
                [],
                '',
                '',
                resolved,
                resolved.rel_path,
            )
        self.assertNotIn('<img', html_out)

    def test_http_link_kept_as_inert_hardened_link(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            html_out = previews._rewrite_epub_html(
                '<a href="https://example.com/">x</a>',
                {},
                [],
                '',
                '',
                resolved,
                resolved.rel_path,
            )
        self.assertIn('rel="noopener noreferrer nofollow"', html_out)

    def test_internal_link_is_stripped_of_href_but_stays_well_formed(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            html_out = previews._rewrite_epub_html(
                '<a href="chapter2.xhtml">x</a>',
                {},
                [],
                '',
                '',
                resolved,
                resolved.rel_path,
            )
        self.assertNotIn('href=', html_out)
        # The document's own </a> must still have a matching opener --
        # swapping in <span> used to leave a dangling </a>.
        self.assertEqual(html_out.count('<a>'), 1)
        self.assertEqual(html_out.count('</a>'), 1)
        self.assertNotIn('<span>', html_out)

    def test_image_alt_text_is_preserved_through_the_rewrite(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        manifest = {'cover-img': {'href': 'cover.jpg', 'media_type': 'image/jpeg', 'properties': []}}
        with paths.resolve_document('a/Book.epub') as resolved:
            html_out = previews._rewrite_epub_html(
                '<img alt="A cover" src="cover.jpg">', manifest, ['cover-img'], '', '', resolved, resolved.rel_path
            )
        self.assertIn('alt="A cover"', html_out)
        self.assertIn('kind=epub-image', html_out)

    def test_image_without_src_is_dropped(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            html_out = previews._rewrite_epub_html(
                '<img alt="no src">', {}, [], '', '', resolved, resolved.rel_path
            )
        self.assertNotIn('<img', html_out)

    def test_script_tag_stripped(self):
        self.assertNotIn('<script', previews._bounded_sanitize('<script>alert(1)</script><p>hi</p>'))

    def test_style_and_on_attrs_stripped(self):
        out = previews._bounded_sanitize('<p onclick="x()" style="color:red">hi</p>')
        self.assertNotIn('onclick', out)
        self.assertNotIn('style', out)


class EpubNestedLayoutBaseDirTests(DocumentViewTestCase):
    """Regression: relative references inside an EPUB content or nav
    document resolve against *that document's own directory*, not the
    OPF's directory. A layout with content nested below the OPF (e.g.
    `Text/chapter.xhtml` referencing `Images/cover.jpg` or
    `Text/nav.xhtml` linking `chapter1.xhtml`) previously lost the image
    and mis-resolved the TOC entry because both were resolved against the
    OPF's directory instead.
    """

    def test_chapter_relative_image_resolves_against_chapter_directory(self):
        self.mkdir('a')
        fixtures.make_epub_nested_layout(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            result = previews.epub_preview(resolved, resolved.rel_path)
        combined_html = ''.join(section['html'] for section in result['sections'])
        self.assertIn('kind=epub-image', combined_html)

    def test_nav_relative_link_resolves_against_nav_directory(self):
        self.mkdir('a')
        fixtures.make_epub_nested_layout(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            result = previews.epub_preview(resolved, resolved.rel_path)
        self.assertEqual(result['toc'], [{'label': 'Chapter One', 'href': 'OEBPS/Text/chapter1.xhtml'}])


class EpubImageSubresourceTests(DocumentViewTestCase):
    def setUp(self):
        super().setUp()
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub', cover_color=(9, 9, 200))

    def _resolved(self):
        return paths.resolve_document('a/Book.epub')

    def test_valid_signed_id_returns_image(self):
        # Build the id the same way epub_preview does, via the rewrite helper.
        with self._resolved() as resolved:
            manifest = {
                'cover-img': {'href': 'cover.jpg', 'media_type': 'image/jpeg', 'properties': ['cover-image']},
            }
            member_ids = ['cover-img']
            html_out = previews._rewrite_epub_html(
                '<img src="cover.jpg">', manifest, member_ids, '', '', resolved, resolved.rel_path
            )
            start = html_out.index('id=') + 3
            end = html_out.index('"', start)
            sub_id = html_out[start:end]
        with self._resolved() as resolved:
            content_type, data = previews.epub_image_subresource(resolved, sub_id)
        self.assertEqual(content_type, 'image/jpeg')
        self.assertTrue(data)

    def test_tampered_signature_rejected(self):
        with self._resolved() as resolved:
            sub_id = subresources.encode(0, resolved)
        tampered = sub_id[:-1] + ('a' if sub_id[-1] != 'a' else 'b')
        with self._resolved() as resolved:
            with self.assertRaises(subresources.SubresourceError):
                previews.epub_image_subresource(resolved, tampered)

    def test_stale_id_after_mtime_change_rejected(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            sub_id = subresources.encode(0, resolved)

        import time

        time.sleep(0.01)
        fixtures.make_epub3(self.root / 'a' / 'Book.epub')  # rewritten -> new mtime/size
        with paths.resolve_document('a/Book.epub') as resolved2:
            with self.assertRaises(subresources.StaleSubresourceError):
                previews.epub_image_subresource(resolved2, sub_id)

    def test_out_of_range_index_rejected(self):
        with self._resolved() as resolved:
            sub_id = subresources.encode(9999, resolved)
        with self._resolved() as resolved:
            with self.assertRaises(previews.PreviewError):
                previews.epub_image_subresource(resolved, sub_id)


class CbzPreviewTests(DocumentViewTestCase):
    def test_default_page_count_includes_ten_samples(self):
        self.mkdir('a')
        fixtures.make_cbz(
            self.root / 'a' / 'Comic.cbz',
            page_colors=[(i, i, i) for i in range(12)],
        )
        with paths.resolve_document('a/Comic.cbz') as resolved:
            count = previews.cbz_preview_page_count(resolved)
        self.assertEqual(count, 10)

    def test_page_count_capped_by_setting(self):
        self.mkdir('a')
        fixtures.make_cbz(
            self.root / 'a' / 'Comic.cbz',
            page_colors=[(i, i, i) for i in range(10)],
        )
        with override_settings(DOCUMENT_VIEWER_MAX_CBZ_PREVIEW_IMAGES=3):
            with paths.resolve_document('a/Comic.cbz') as resolved:
                count = previews.cbz_preview_page_count(resolved)
        self.assertEqual(count, 3)

    def test_each_page_bounded_image_checked(self):
        self.mkdir('a')
        fixtures.make_cbz(self.root / 'a' / 'Comic.cbz', page_colors=[(1, 2, 3)])
        with paths.resolve_document('a/Comic.cbz') as resolved:
            data = previews.cbz_preview_page(resolved, 1)
        self.assertTrue(data)

    def test_page_out_of_range_raises(self):
        self.mkdir('a')
        fixtures.make_cbz(self.root / 'a' / 'Comic.cbz', page_colors=[(1, 2, 3)])
        with paths.resolve_document('a/Comic.cbz') as resolved:
            with self.assertRaises(previews.PreviewError):
                previews.cbz_preview_page(resolved, 5)

    def test_undecodable_page_skipped_not_fatal(self):
        self.mkdir('a')
        import zipfile

        with zipfile.ZipFile(self.root / 'a' / 'Comic.cbz', 'w') as zf:
            zf.writestr('page001.jpg', b'not a real jpeg')
        with paths.resolve_document('a/Comic.cbz') as resolved:
            with self.assertRaises(previews.PreviewError):
                # A single undecodable page in a 1-page book has nothing to
                # fall back to, but must raise our own typed error, not crash.
                previews.cbz_preview_page(resolved, 1)


class PdfPreviewTests(DocumentViewTestCase):
    def test_page_count_reflects_renderer_output(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        with paths.resolve_document('a/Doc.pdf') as resolved:
            count = previews.pdf_preview_pages(resolved)
        self.assertEqual(count, 1)

    def test_page_count_zero_when_renderer_missing(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        with mock.patch.object(pdfrender, '_PDFTOPPM', None):
            with paths.resolve_document('a/Doc.pdf') as resolved:
                count = previews.pdf_preview_pages(resolved)
        self.assertEqual(count, 0)

    def test_renderer_timeout_degrades_without_raising_from_pages(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        with mock.patch(
            'documentview.pdfrender.render_pdf_pages',
            side_effect=pdfrender.PdfRenderError('timed out'),
        ):
            with paths.resolve_document('a/Doc.pdf') as resolved:
                count = previews.pdf_preview_pages(resolved)
        self.assertEqual(count, 0)

    def test_detail_page_costs_one_render_not_one_per_page(self):
        # Regression: counting pages used to render the whole preview and
        # throw it away, then every page image re-rendered from scratch --
        # 4 pdftoppm spawns to display one 3-page preview.
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')

        with mock.patch.object(
            pdfrender, 'render_pdf_pages', wraps=pdfrender.render_pdf_pages
        ) as spy:
            with paths.resolve_document('a/Doc.pdf') as resolved:
                count = previews.pdf_preview_pages(resolved)
                for page in range(1, count + 1):
                    self.assertTrue(previews.pdf_preview_page(resolved, page))
        self.assertEqual(spy.call_count, 1)

    def test_second_request_reuses_the_cache_without_rendering(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        with paths.resolve_document('a/Doc.pdf') as resolved:
            previews.pdf_preview_pages(resolved)

        with mock.patch.object(pdfrender, 'render_pdf_pages') as spy:
            with paths.resolve_document('a/Doc.pdf') as resolved:
                self.assertEqual(previews.pdf_preview_pages(resolved), 1)
                self.assertTrue(previews.pdf_preview_page(resolved, 1))
        spy.assert_not_called()

    def test_edited_pdf_is_not_served_from_the_stale_cache(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf', color=(200, 10, 10))
        with paths.resolve_document('a/Doc.pdf') as resolved:
            first = previews.pdf_preview_page(resolved, 1)

        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf', size=(120, 200), color=(10, 200, 10))
        with paths.resolve_document('a/Doc.pdf') as resolved:
            second = previews.pdf_preview_page(resolved, 1)
        self.assertNotEqual(first, second)

    def test_failed_render_is_not_cached_so_a_later_request_retries(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        with mock.patch.object(pdfrender, '_PDFTOPPM', None):
            with paths.resolve_document('a/Doc.pdf') as resolved:
                self.assertEqual(previews.pdf_preview_pages(resolved), 0)
        with paths.resolve_document('a/Doc.pdf') as resolved:
            self.assertEqual(previews.pdf_preview_pages(resolved), 1)

    def test_page_dimension_bounded_for_huge_declared_page(self):
        from PIL import Image

        self.mkdir('a')
        # A physically huge page (in points); pdftoppm's own -scale-to cap
        # must keep the rendered raster within the configured dimension
        # regardless of the declared page size.
        img = Image.new('RGB', (3000, 4000), (5, 5, 5))
        img.save(self.root / 'a' / 'Doc.pdf', format='PDF')
        with override_settings(DOCUMENT_VIEWER_MAX_PDF_RENDER_DIMENSION=200):
            with paths.resolve_document('a/Doc.pdf') as resolved:
                data = previews.pdf_preview_page(resolved, 1)
        out = Image.open(io.BytesIO(data))
        self.assertLessEqual(max(out.size), 200)


class MarkdownTextPreviewTests(DocumentViewTestCase):
    def test_markdown_script_stripped(self):
        self.touch('a/Notes.md', b'# Title\n\n<script>alert(1)</script>\n\nHello *world*.')
        with paths.resolve_document('a/Notes.md') as resolved:
            html_out = previews.markdown_preview(resolved)
        self.assertNotIn('<script', html_out)
        self.assertIn('Hello', html_out)

    def test_markdown_keeps_http_image_and_link(self):
        self.touch(
            'a/Notes.md',
            b'![alt](https://example.com/x.png)\n\n[link](https://example.com/)',
        )
        with paths.resolve_document('a/Notes.md') as resolved:
            html_out = previews.markdown_preview(resolved)
        self.assertIn('https://example.com/x.png', html_out)
        self.assertIn('https://example.com/', html_out)

    def test_markdown_bounded_by_bytes(self):
        self.touch('a/Notes.md', b'x' * 10000)
        with override_settings(DOCUMENT_VIEWER_MAX_PREVIEW_BYTES=10):
            with paths.resolve_document('a/Notes.md') as resolved:
                html_out = previews.markdown_preview(resolved)
        self.assertLess(len(html_out), 200)

    def test_text_preview_bounded_and_decode_errors_replaced(self):
        self.touch('a/Notes.txt', b'hello \xff\xfe world')
        with paths.resolve_document('a/Notes.txt') as resolved:
            text_out = previews.text_preview(resolved)
        self.assertIn('hello', text_out)
        self.assertIn('world', text_out)

    def test_text_preview_bounded_by_bytes(self):
        self.touch('a/Notes.txt', b'x' * 10000)
        with override_settings(DOCUMENT_VIEWER_MAX_PREVIEW_BYTES=10):
            with paths.resolve_document('a/Notes.txt') as resolved:
                text_out = previews.text_preview(resolved)
        self.assertEqual(len(text_out), 10)


class SubresourceSigningTests(DocumentViewTestCase):
    def test_encode_decode_roundtrip(self):
        self.touch('a/Notes.txt', b'hello')
        with paths.resolve_document('a/Notes.txt') as resolved:
            sub_id = subresources.encode(3, resolved)
            self.assertEqual(subresources.decode(sub_id, resolved), 3)

    def test_bad_signature_rejected(self):
        self.touch('a/Notes.txt', b'hello')
        with paths.resolve_document('a/Notes.txt') as resolved:
            with self.assertRaises(subresources.SubresourceError):
                subresources.decode('not-a-real-signed-value', resolved)

    def test_negative_index_rejected(self):
        self.touch('a/Notes.txt', b'hello')
        with paths.resolve_document('a/Notes.txt') as resolved:
            forged = signing.dumps({'i': -1, 'fp': subresources.fingerprint(resolved)}, salt='documentview.subresource')
            with self.assertRaises(subresources.SubresourceError):
                subresources.decode(forged, resolved)


class EpubSvgImageRejectionTests(DocumentViewTestCase):
    """Regression: an EPUB manifest item declared image/svg+xml must never
    be served as a preview subresource, since (unlike a raster image) SVG
    can carry <script>/event-handler content that executes if the resource
    is ever opened directly in this app's authenticated origin -- and must
    never even be linked to from the sanitized chapter HTML in the first
    place.
    """

    def test_epub_image_subresource_rejects_svg_media_type(self):
        self.mkdir('a')
        fixtures.make_epub_with_svg_image(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            sub_id = subresources.encode(0, resolved)  # "evil-svg" is manifest index 0
            with self.assertRaises(previews.PreviewError):
                previews.epub_image_subresource(resolved, sub_id)

    def test_rewrite_drops_svg_img_even_though_href_matches_manifest(self):
        self.mkdir('a')
        fixtures.make_epub_with_svg_image(self.root / 'a' / 'Book.epub')
        manifest = {'evil-svg': {'href': 'evil.svg', 'media_type': 'image/svg+xml', 'properties': []}}
        with paths.resolve_document('a/Book.epub') as resolved:
            html_out = previews._rewrite_epub_html(
                '<img src="evil.svg">', manifest, ['evil-svg'], '', '', resolved, resolved.rel_path
            )
        self.assertNotIn('<img', html_out)

    def test_full_epub_preview_never_links_the_svg(self):
        self.mkdir('a')
        fixtures.make_epub_with_svg_image(self.root / 'a' / 'Book.epub')
        with paths.resolve_document('a/Book.epub') as resolved:
            result = previews.epub_preview(resolved, resolved.rel_path)
        combined_html = ''.join(section['html'] for section in result['sections'])
        self.assertNotIn('<img', combined_html)
        self.assertNotIn('kind=epub-image', combined_html)

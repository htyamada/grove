import io
from unittest import mock

from django.test import override_settings
from PIL import Image

from .. import covers, documents, images, pdfrender
from . import fixtures
from .base import DocumentViewTestCase


def _scan(root, rel):
    abs_dir = root / rel if rel else root
    return documents.scan_directory(abs_dir, rel)


def _decode(data):
    return Image.open(io.BytesIO(data)).convert('RGB')


def _center_pixel(img):
    w, h = img.size
    return img.getpixel((w // 2, h // 2))


def _close(a, b, tol=40):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class CoverExtractionTests(DocumentViewTestCase):
    def test_epub3_cover_image_property(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub', cover_color=(200, 30, 30))
        _, docs = _scan(self.root, 'a')
        doc = next(d for d in docs if d.basename == 'Book')
        data = covers.cover_for(doc, 'thumb')
        pixel = _center_pixel(_decode(data))
        self.assertTrue(_close(pixel, (200, 30, 30)), pixel)

    def test_epub2_meta_cover(self):
        self.mkdir('a')
        fixtures.make_epub2(self.root / 'a' / 'Book.epub', cover_color=(30, 200, 30))
        _, docs = _scan(self.root, 'a')
        doc = docs[0]
        data = covers.cover_for(doc, 'thumb')
        pixel = _center_pixel(_decode(data))
        self.assertTrue(_close(pixel, (30, 200, 30)), pixel)

    def test_cbz_first_image_in_natural_order(self):
        self.mkdir('a')
        fixtures.make_cbz(self.root / 'a' / 'Comic.cbz', page_colors=((10, 10, 200), (10, 200, 10)))
        _, docs = _scan(self.root, 'a')
        doc = docs[0]
        data = covers.cover_for(doc, 'thumb')
        pixel = _center_pixel(_decode(data))
        self.assertTrue(_close(pixel, (10, 10, 200)), pixel)

    def test_pdf_cover_first_page(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf', color=(50, 100, 150))
        _, docs = _scan(self.root, 'a')
        doc = docs[0]
        data = covers.cover_for(doc, 'thumb')
        pixel = _center_pixel(_decode(data))
        self.assertTrue(_close(pixel, (50, 100, 150), tol=60), pixel)

    def test_missing_cover_falls_back_to_generic(self):
        self.mkdir('a')
        fixtures.make_epub_no_cover(self.root / 'a' / 'Book.epub')
        _, docs = _scan(self.root, 'a')
        doc = docs[0]
        data = covers.cover_for(doc, 'thumb')
        img = _decode(data)
        self.assertEqual(img.size, (150, 220))

    def test_malformed_zip_falls_back_to_generic(self):
        self.mkdir('a')
        fixtures.make_malformed_zip(self.root / 'a' / 'Book.epub')
        _, docs = _scan(self.root, 'a')
        doc = docs[0]
        data = covers.cover_for(doc, 'thumb')
        img = _decode(data)
        self.assertEqual(img.size, (150, 220))

    def test_encrypted_zip_falls_back_to_generic(self):
        self.mkdir('a')
        fixtures.make_encrypted_zip(self.root / 'a' / 'Comic.cbz')
        _, docs = _scan(self.root, 'a')
        doc = docs[0]
        data = covers.cover_for(doc, 'thumb')
        img = _decode(data)
        self.assertEqual(img.size, (150, 220))

    def test_txt_and_md_use_generic_cover(self):
        self.touch('a/Notes.txt', b'hello')
        _, docs = _scan(self.root, 'a')
        doc = docs[0]
        data = covers.cover_for(doc, 'thumb')
        img = _decode(data)
        self.assertEqual(img.size, (150, 220))


class CompressionBombTests(DocumentViewTestCase):
    def test_compressed_bytes_bomb_rejected(self):
        self.mkdir('a')
        path = self.root / 'a' / 'Bomb.cbz'
        import zipfile

        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('page001.jpg', b'\x00' * (2 * 1024 * 1024))

        with override_settings(
            DOCUMENT_VIEWER_MAX_COMPRESSION_RATIO=10,
            DOCUMENT_VIEWER_MAX_ENTRY_BYTES=1024 * 1024,
        ):
            _, docs = _scan(self.root, 'a')
            doc = docs[0]
            # Cover extraction must not blow up the process; a rejected
            # member falls back to the generic cover rather than raising.
            data = covers.cover_for(doc, 'thumb')
            img = _decode(data)
            self.assertEqual(img.size, (150, 220))

    def test_decoded_pixel_bomb_rejected_by_own_check(self):
        # A legitimately-decodable image whose pixel count exceeds the
        # configured cap must be rejected without ever touching the
        # process-global PIL.Image.MAX_IMAGE_PIXELS.
        buf = io.BytesIO()
        Image.new('RGB', (64, 64), (1, 2, 3)).save(buf, format='JPEG')
        data = buf.getvalue()
        with override_settings(DOCUMENT_VIEWER_MAX_IMAGE_PIXELS=100):
            with self.assertRaises(images.ImageDecodeError):
                images.bounded_image_open(data)

    def test_max_image_pixels_global_untouched(self):
        original = Image.MAX_IMAGE_PIXELS
        buf = io.BytesIO()
        Image.new('RGB', (64, 64), (1, 2, 3)).save(buf, format='JPEG')
        with override_settings(DOCUMENT_VIEWER_MAX_IMAGE_PIXELS=100):
            try:
                images.bounded_image_open(buf.getvalue())
            except images.ImageDecodeError:
                pass
        self.assertEqual(Image.MAX_IMAGE_PIXELS, original)


class PdfRendererAbsentTests(DocumentViewTestCase):
    def test_cover_falls_back_when_pdftoppm_missing(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Doc.pdf')
        _, docs = _scan(self.root, 'a')
        doc = docs[0]
        with mock.patch.object(pdfrender, '_PDFTOPPM', None):
            data = covers.cover_for(doc, 'thumb')
        img = _decode(data)
        self.assertEqual(img.size, (150, 220))


class DeterministicPreferenceTests(DocumentViewTestCase):
    def test_epub_preferred_over_pdf(self):
        self.mkdir('a')
        fixtures.make_epub3(self.root / 'a' / 'Book.epub', cover_color=(9, 200, 9))
        fixtures.make_pdf(self.root / 'a' / 'Book.pdf', color=(200, 9, 9))
        _, docs = _scan(self.root, 'a')
        doc = docs[0]
        data = covers.cover_for(doc, 'thumb')
        pixel = _center_pixel(_decode(data))
        self.assertTrue(_close(pixel, (9, 200, 9)), pixel)


class CacheInvalidationTests(DocumentViewTestCase):
    def test_adding_preferred_variant_changes_cover(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Book.pdf', color=(200, 9, 9))
        _, docs = _scan(self.root, 'a')
        first = covers.cover_for(docs[0], 'thumb')
        first_pixel = _center_pixel(_decode(first))
        self.assertTrue(_close(first_pixel, (200, 9, 9)), first_pixel)

        fixtures.make_epub3(self.root / 'a' / 'Book.epub', cover_color=(9, 200, 9))
        _, docs2 = _scan(self.root, 'a')
        second = covers.cover_for(docs2[0], 'thumb')
        second_pixel = _center_pixel(_decode(second))
        self.assertTrue(_close(second_pixel, (9, 200, 9)), second_pixel)
        self.assertNotEqual(first, second)

    def test_refresh_invalidates_cache_for_all_sizes(self):
        self.mkdir('a')
        fixtures.make_pdf(self.root / 'a' / 'Book.pdf', color=(200, 9, 9))
        _, docs = _scan(self.root, 'a')
        doc = docs[0]
        covers.cover_for(doc, 'thumb')
        covers.cover_for(doc, 'detail')
        for size_name in ('thumb', 'detail'):
            self.assertTrue(covers._cache_path(doc, size_name).exists())
        covers.invalidate(doc)
        for size_name in ('thumb', 'detail'):
            self.assertFalse(covers._cache_path(doc, size_name).exists())


class FitAndPadTests(DocumentViewTestCase):
    def test_wider_than_box_no_crop_no_stretch(self):
        img = Image.new('RGB', (400, 100), (10, 10, 10))
        out = covers._fit_and_pad(img, (150, 220))
        self.assertEqual(out.size, (150, 220))
        # Padded rows top/bottom should be the pad color, not stretched content.
        self.assertEqual(out.getpixel((75, 0)), covers._PAD_COLOR)

    def test_taller_than_box_no_crop_no_stretch(self):
        img = Image.new('RGB', (100, 400), (10, 10, 10))
        out = covers._fit_and_pad(img, (150, 220))
        self.assertEqual(out.size, (150, 220))
        self.assertEqual(out.getpixel((0, 110)), covers._PAD_COLOR)

    def test_matching_aspect_ratio_fills_box(self):
        img = Image.new('RGB', (150, 220), (10, 10, 10))
        out = covers._fit_and_pad(img, (150, 220))
        self.assertEqual(out.size, (150, 220))
        self.assertEqual(out.getpixel((0, 0)), (10, 10, 10))
        self.assertEqual(out.getpixel((149, 219)), (10, 10, 10))

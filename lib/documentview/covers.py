"""Cover extraction, deterministic selection, caching, and fit-and-pad.

`cover_for()` walks `documents.FORMAT_PREFERENCE` -- the same ordering
`documents.representative_variant()` uses, not a second one -- trying each
available variant in turn for a usable cover image, and falls back to a
generic cover on any extraction, decoding, or rendering failure. Missing or
malformed covers must never break browsing (spec 1.2).
"""
import contextlib
import hashlib
import io
import logging
import os
import textwrap

from PIL import Image, ImageDraw

from . import archives, config, documents, epub, images, paths, pdfrender

logger = logging.getLogger('documentview')

EXTRACTOR_VERSION = 1

_PAD_COLOR = (26, 34, 51)
_GENERIC_CANVAS = (300, 440)


class CoverError(Exception):
    pass


def _open_variant_stream(variant):
    resolved = paths.resolve_document(variant.rel_path)
    try:
        fd = os.dup(resolved.fd)
    finally:
        resolved.close()
    return os.fdopen(fd, 'rb')


@contextlib.contextmanager
def _open_archive(variant):
    """Open one variant as a bounded ZIP archive, closing both the reader
    and the underlying stream on the way out.
    """
    with _open_variant_stream(variant) as fh:
        with archives.SafeZipReader(fh) as reader:
            yield reader


def _epub_cover_href(opf_root, manifest, opf_dir):
    for item_id, info in manifest.items():
        if 'cover-image' in info['properties'] and info['href']:
            return epub.resolve_href(opf_dir, info['href'])

    metadata = opf_root.find('opf:metadata', epub.NS)
    if metadata is not None:
        for meta in metadata.findall('opf:meta', epub.NS):
            if meta.get('name') == 'cover':
                info = manifest.get(meta.get('content'))
                if info is not None and info['href']:
                    return epub.resolve_href(opf_dir, info['href'])

    href = epub.guide_reference_href(opf_root, {'cover'})
    if href:
        return epub.resolve_href(opf_dir, href)
    return None


def _epub_cover(variant):
    with _open_archive(variant) as reader:
        try:
            opf_root, _opf_path, opf_dir = epub.open_package(reader)
        except epub.EpubStructureError:
            return None
        manifest = epub.manifest_items(opf_root)

        href = _epub_cover_href(opf_root, manifest, opf_dir)
        if not href or href not in reader.names():
            return None
        data = reader.read(href)
    return images.bounded_image_open(data)


def _cbz_cover(variant):
    with _open_archive(variant) as reader:
        names = reader.image_names_natural_order(documents.CBZ_IMAGE_SUFFIXES)
        if not names:
            return None
        data = reader.read(names[0])
    return images.bounded_image_open(data)


def _pdf_cover(variant):
    max_dim = config.limit('DOCUMENT_VIEWER_MAX_PDF_RENDER_DIMENSION')
    with paths.resolve_document(variant.rel_path) as resolved:
        pages = pdfrender.render_pdf_pages(resolved.fd, 1, 1)
    if not pages:
        return None
    img = pages[0]
    img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    return img


_EXTRACTORS = {
    'epub': _epub_cover,
    'cbz': _cbz_cover,
    'pdf': _pdf_cover,
}


def _generic_cover(document):
    img = Image.new('RGB', _GENERIC_CANVAS, _PAD_COLOR)
    draw = ImageDraw.Draw(img)
    rep = documents.representative_variant(document)
    title = document.title
    if len(title) > 200:
        title = title[:197] + '...'
    lines = textwrap.wrap(title, width=18)[:12] or [title[:18]]
    y = 24
    for line in lines:
        draw.text((16, y), line, fill=(220, 225, 235))
        y += 20
    draw.text((16, _GENERIC_CANVAS[1] - 32), rep.suffix.upper(), fill=(140, 165, 200))
    return img


def _extract_raw_cover(document):
    for suffix in documents.FORMAT_PREFERENCE:
        variant = document.variants.get(suffix)
        extractor = _EXTRACTORS.get(suffix)
        if variant is None or extractor is None:
            continue
        try:
            img = extractor(variant)
        except Exception as e:
            logger.warning('cover extraction failed for %s: %s', variant.rel_path, e)
            img = None
        if img is not None:
            return img
    return _generic_cover(document)


def _fit_and_pad(img, box):
    box_w, box_h = box
    img = img.convert('RGB')
    src_w, src_h = img.size
    scale = min(box_w / src_w, box_h / src_h)
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new('RGB', (box_w, box_h), _PAD_COLOR)
    canvas.paste(resized, ((box_w - new_w) // 2, (box_h - new_h) // 2))
    return canvas


def _encode_jpeg(img):
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    return buf.getvalue()


def _cache_key(document, size_name):
    rep = documents.representative_variant(document)
    candidates = tuple(sorted((v.rel_path, v.mtime_ns, v.size) for v in document.variants.values()))
    payload = repr((rep.rel_path, rep.mtime_ns, rep.size, candidates, size_name, EXTRACTOR_VERSION))
    return hashlib.sha256(payload.encode()).hexdigest()


def _cache_path(document, size_name):
    key = _cache_key(document, size_name)
    return config.cache_dir() / 'covers' / key[:2] / f'{key}.jpg'


def cover_for(document, size_name: str) -> bytes:
    config.validate_live()
    sizes = config.cover_sizes()
    if size_name not in sizes:
        raise CoverError(f'unknown cover size: {size_name}')
    box = tuple(sizes[size_name])

    cache_path = _cache_path(document, size_name)
    try:
        return cache_path.read_bytes()
    except OSError:
        pass

    try:
        raw = _extract_raw_cover(document)
    except Exception as e:
        logger.warning('cover extraction raised for %s: %s', document.basename, e)
        raw = _generic_cover(document)

    data = _encode_jpeg(_fit_and_pad(raw, box))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_name(f'{cache_path.name}.{os.getpid()}.tmp')
    tmp_path.write_bytes(data)
    os.replace(tmp_path, cache_path)
    return data


def invalidate(document) -> None:
    config.validate_live()
    for size_name in config.cover_sizes():
        _cache_path(document, size_name).unlink(missing_ok=True)

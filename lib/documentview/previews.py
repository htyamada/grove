"""Bounded, format-appropriate previews (spec 1.3, 4.4).

Preview exists only to support browsing and selection; it favors low
latency over completeness and is not a full-document reading experience.
"""
import contextlib
import hashlib
import html
import io
import os
import posixpath
import re

import bleach
import markdown as markdown_lib
from django.urls import reverse

from . import archives, config, documents, epub, images, pdfrender, subresources

# Shared EPUB/Markdown allowlist (spec 4.4): <script>, all on* attributes,
# <iframe>, <object>, <embed>, <form>, <svg>, <style>/inline style=, and
# data: URLs are always stripped by omission from these lists and by
# restricting `protocols` to http/https below.
_ALLOWED_TAGS = [
    'p', 'br', 'hr', 'strong', 'em', 'b', 'i', 'u', 's', 'blockquote', 'code', 'pre',
    'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'img', 'table',
    'thead', 'tbody', 'tr', 'th', 'td', 'span', 'div',
]
_ALLOWED_ATTRS = {
    'a': ['href', 'title', 'rel'],
    'img': ['src', 'alt', 'title'],
    '*': ['id'],
}

# Raster formats only. SVG is deliberately excluded even though its MIME
# type starts with "image/": unlike a raster image, an SVG document can
# carry <script> and event-handler attributes that execute if the resource
# is ever opened directly (not just embedded via <img>), so serving one
# from this app's authenticated origin would let a malicious EPUB run
# script there. `X-Content-Type-Options: nosniff` guards against a
# mismatched declared-vs-actual type; it does nothing for a type that is
# genuinely, correctly SVG.
_SAFE_EPUB_IMAGE_TYPES = frozenset({
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp',
})

# Presentation caps for the TOC listing itself (not safety limits -- the
# bytes behind them are already bounded by DOCUMENT_VIEWER_MAX_XML_BYTES).
_MAX_TOC_ENTRIES = 200
_MAX_TOC_LABEL_CHARS = 200

_BODY_RE = re.compile(r'<body[^>]*>(.*)</body>', re.IGNORECASE | re.DOTALL)
_IMG_TAG_RE = re.compile(r'<img\b[^>]*>', re.IGNORECASE)
_A_HREF_RE = re.compile(r'<a\b([^>]*?)\shref="([^"]*)"([^>]*)>', re.IGNORECASE)
_ATTR_RE = re.compile(r'\b([A-Za-z_:][-\w:.]*)="([^"]*)"')

_CHAPTER_LABEL_RE = re.compile(r'\bchapter\s+(?:\d+|[ivxlcdm]+|one|two|three)\b', re.IGNORECASE)
_FRONT_MATTER_LABEL_RE = re.compile(
    r'^(?:cover|title(?: page)?|table of contents|contents|copyright|'
    r'revision history|dedication|acknowledg(?:e)?ments|colophon)$',
    re.IGNORECASE,
)


class PreviewError(Exception):
    pass


def _open_stream(resolved):
    fh = os.fdopen(os.dup(resolved.fd), 'rb')
    fh.seek(0)
    return fh


@contextlib.contextmanager
def _open_archive(resolved):
    """Open a resolved document as a bounded ZIP archive, closing both the
    reader and the underlying stream on the way out.
    """
    with _open_stream(resolved) as fh:
        with archives.SafeZipReader(fh) as reader:
            yield reader


def _bounded_sanitize(html_source: str) -> str:
    return bleach.clean(
        html_source,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=['http', 'https'],
        strip=True,
    )


# ---------------------------------------------------------------------------
# Markdown / text
# ---------------------------------------------------------------------------

def markdown_preview(resolved) -> str:
    """Byte-capped read, `markdown.markdown()` (core extensions only),
    **then `bleach.clean()` as the actual enforcement point**:
    Python-Markdown passes inline raw HTML through unchanged by default, so
    it is bleach that removes any script/event-handler/raw-HTML the source
    embedded and restricts link/image schemes to http/https. Markdown
    files are locally authored and trusted to reference external HTTP(S)
    images and links (spec 1.3), so -- unlike EPUB below -- src/href are
    left as-is once sanitized.
    """
    max_bytes = config.limit('DOCUMENT_VIEWER_MAX_PREVIEW_BYTES')
    raw = os.pread(resolved.fd, max_bytes, 0)
    text = raw.decode('utf-8', errors='replace')
    rendered = markdown_lib.markdown(text)
    return _bounded_sanitize(rendered)


def text_preview(resolved) -> str:
    """Bounded read, `errors='replace'`. Django's template autoescaping
    renders this as escaped UTF-8 when interpolated into a `<pre>`.
    """
    max_bytes = config.limit('DOCUMENT_VIEWER_MAX_PREVIEW_BYTES')
    raw = os.pread(resolved.fd, max_bytes, 0)
    return raw.decode('utf-8', errors='replace')


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------

def _nav_toc_entries(reader, opf_dir, manifest):
    nav_id = next((i for i, info in manifest.items() if 'nav' in info['properties']), None)
    if nav_id is None:
        return None
    href = manifest[nav_id]['href']
    if not href:
        return None
    member = epub.resolve_href(opf_dir, href)
    if member not in reader.names():
        return None
    try:
        raw = reader.read_text_xml(member)
    except archives.ArchiveError:
        return None
    # A nav document's own <a href> targets are relative to *its own*
    # location, not the OPF's -- only the manifest-declared href to the nav
    # document itself is OPF-relative.
    nav_dir = posixpath.dirname(member)
    entries = []
    for m in re.finditer(r'<a[^>]*\shref="([^"]*)"[^>]*>(.*?)</a>', raw.decode('utf-8', errors='replace'), re.DOTALL):
        href_val, label_html = m.groups()
        label = re.sub(r'<[^>]+>', '', label_html).strip()
        if label:
            entries.append({'label': label[:_MAX_TOC_LABEL_CHARS], 'href': epub.resolve_href(nav_dir, href_val)})
    return entries or None


def _ncx_toc_entries(reader, opf_root, opf_dir, manifest):
    toc_id = epub.spine_toc_id(opf_root)
    if not toc_id:
        return None
    info = manifest.get(toc_id)
    if info is None or not info['href']:
        return None
    member = epub.resolve_href(opf_dir, info['href'])
    if member not in reader.names():
        return None
    try:
        raw = reader.read_text_xml(member)
    except archives.ArchiveError:
        return None
    # Same reasoning as the nav case: <content src> in the NCX is relative
    # to the NCX document's own location.
    ncx_dir = posixpath.dirname(member)
    text = raw.decode('utf-8', errors='replace')
    entries = []
    for m in re.finditer(
        r'<navLabel>\s*<text>(.*?)</text>\s*</navLabel>\s*<content\s+src="([^"]*)"',
        text,
        re.DOTALL,
    ):
        label, href_val = m.groups()
        label = label.strip()
        if label:
            entries.append({'label': label[:_MAX_TOC_LABEL_CHARS], 'href': epub.resolve_href(ncx_dir, href_val)})
    return entries or None


def _spine_fallback_toc(manifest, spine, opf_dir):
    entries = []
    for idref in spine:
        info = manifest.get(idref)
        if info is None or not info['href'] or 'nav' in info['properties']:
            continue
        entries.append({'label': info['href'], 'href': epub.resolve_href(opf_dir, info['href'])})
    return entries


def _first_substantive_toc_href(toc, spine_hrefs):
    """Prefer an explicitly labelled chapter, falling back to the first
    TOC entry that is not recognizable front matter.
    """
    entries = [entry for entry in toc if entry['href'] in spine_hrefs]
    for entry in entries:
        if _CHAPTER_LABEL_RE.search(entry['label']):
            return entry['href']
    for entry in entries:
        if not _FRONT_MATTER_LABEL_RE.fullmatch(entry['label'].strip()):
            return entry['href']
    return None


def _select_preview_hrefs(opf_root, manifest, spine, opf_dir, toc, max_sections):
    if max_sections <= 0:
        return []

    hrefs = []

    preface_href = epub.guide_reference_href(opf_root, {'preface', 'introduction', 'foreword'})
    if preface_href:
        hrefs.append(epub.resolve_href(opf_dir, preface_href))

    nav_ids = {i for i, info in manifest.items() if 'nav' in info['properties']}
    spine_hrefs = []
    for idref in spine:
        if idref in nav_ids:
            continue
        info = manifest.get(idref)
        if info is None or not info['href']:
            continue
        candidate = epub.resolve_href(opf_dir, info['href'])
        spine_hrefs.append(candidate)

    chapter_href = _first_substantive_toc_href(toc, set(spine_hrefs))
    # Reserve one of the bounded preview slots for the first semantically
    # identified chapter. Otherwise cover/contents/copyright pages at the
    # head of the spine can consume the whole preview budget.
    fill_limit = max_sections - 1 if chapter_href else max_sections
    for candidate in spine_hrefs:
        if len(hrefs) >= fill_limit:
            break
        if candidate == chapter_href:
            continue
        if candidate in hrefs:
            continue
        hrefs.append(candidate)

    if chapter_href and chapter_href not in hrefs:
        hrefs.append(chapter_href)

    return hrefs[:max_sections]


def _rewrite_epub_html(html_fragment, manifest, member_ids, opf_dir, content_dir, resolved, rel_path):
    """Rewrite `<img src>` to an internal validated preview-subresource URL
    when it names a real manifest item (dropped otherwise); strip remote
    resource loads; keep `http(s)` `<a href>` as inert, hardened
    text-with-link. Runs *after* `bleach.clean()` has already removed
    scripts/dangerous tags -- this only adjusts already-safe img/a
    attribute values.

    `opf_dir` locates manifest-declared hrefs (always OPF-relative);
    `content_dir` -- the directory of the section document currently being
    rewritten -- locates `<img src>` values found *inside* that document's
    own markup, since XHTML content resolves references relative to
    itself, not to the OPF. A layout like `Text/chapter.xhtml` referencing
    `../Images/cover.jpg` only resolves correctly against `content_dir`.
    """
    base_url = reverse('documentview:preview', kwargs={'rel_path': rel_path})
    href_to_index = {}
    for position, item_id in enumerate(member_ids):
        info = manifest[item_id]
        if info['href'] and info['media_type'] in _SAFE_EPUB_IMAGE_TYPES:
            href_to_index[epub.resolve_href(opf_dir, info['href'])] = position

    def img_sub(m):
        attrs = dict(_ATTR_RE.findall(m.group(0)))
        src = attrs.get('src')
        if src is None:
            return ''
        candidate = epub.resolve_href(content_dir, html.unescape(src))
        index = href_to_index.get(candidate)
        if index is None:
            return ''
        sub_id = subresources.encode(index, resolved)
        url = f'{base_url}?kind=epub-image&id={sub_id}'
        # `alt` comes straight from bleach's already-escaped output, so it is
        # re-emitted as-is rather than escaped a second time.
        alt = attrs.get('alt', '')
        return f'<img src="{html.escape(url, quote=True)}" alt="{alt}">'

    def a_sub(m):
        _pre, href_val, _post = m.groups()
        href_val = html.unescape(href_val)
        if href_val.startswith('http://') or href_val.startswith('https://'):
            safe_href = html.escape(href_val, quote=True)
            return f'<a href="{safe_href}" rel="noopener noreferrer nofollow">'
        # An internal EPUB link goes nowhere in a bounded preview. Drop just
        # the href, keeping the <a> element itself so the document's own
        # matching </a> stays paired and the fragment remains well-formed.
        return '<a>'

    out = _IMG_TAG_RE.sub(img_sub, html_fragment)
    out = _A_HREF_RE.sub(a_sub, out)
    return out


def epub_preview(resolved, rel_path: str) -> dict:
    """Returns `{'toc': [...], 'sections': [{'label', 'html'}, ...]}`,
    capped at `DOCUMENT_VIEWER_MAX_PREVIEW_SECTIONS` sections, each capped
    at `DOCUMENT_VIEWER_MAX_PREVIEW_BYTES`. Never raises -- malformed
    navigation degrades to the spine heuristic / an empty TOC rather than
    breaking the preview.
    """
    max_sections = config.limit('DOCUMENT_VIEWER_MAX_PREVIEW_SECTIONS')
    max_bytes = config.limit('DOCUMENT_VIEWER_MAX_PREVIEW_BYTES')

    try:
        with _open_archive(resolved) as reader:
            return _epub_preview_from_archive(reader, resolved, rel_path, max_sections, max_bytes)
    except (archives.ArchiveError, epub.EpubStructureError):
        return {'toc': [], 'sections': []}


def _epub_preview_from_archive(reader, resolved, rel_path, max_sections, max_bytes):
    opf_root, _opf_path, opf_dir = epub.open_package(reader)
    manifest = epub.manifest_items(opf_root)
    member_ids = list(manifest)
    spine = epub.spine_idrefs(opf_root)

    toc = None
    try:
        toc = _nav_toc_entries(reader, opf_dir, manifest)
    except Exception:
        toc = None
    if toc is None:
        try:
            toc = _ncx_toc_entries(reader, opf_root, opf_dir, manifest)
        except Exception:
            toc = None
    if toc is None:
        toc = _spine_fallback_toc(manifest, spine, opf_dir)

    hrefs = _select_preview_hrefs(opf_root, manifest, spine, opf_dir, toc, max_sections)

    sections = []
    for href in hrefs:
        if href not in reader.names():
            continue
        try:
            raw = reader.read(href, max_len=max_bytes)
        except archives.ArchiveError:
            continue
        text = raw.decode('utf-8', errors='replace')
        body_match = _BODY_RE.search(text)
        fragment = body_match.group(1) if body_match else text
        safe_html = _bounded_sanitize(fragment)
        safe_html = _rewrite_epub_html(
            safe_html, manifest, member_ids, opf_dir, posixpath.dirname(href), resolved, rel_path
        )
        sections.append({'label': posixpath.basename(href), 'html': safe_html})

    return {'toc': toc[:_MAX_TOC_ENTRIES], 'sections': sections}


def epub_image_subresource(resolved, id_str: str):
    """Returns `(content_type, data)` for a signed EPUB manifest-item id,
    or raises `subresources.SubresourceError` / `PreviewError`.
    """
    with _open_archive(resolved) as reader:
        try:
            opf_root, _opf_path, opf_dir = epub.open_package(reader)
        except epub.EpubStructureError as e:
            raise PreviewError('malformed epub') from e
        manifest = epub.manifest_items(opf_root)
        member_ids = list(manifest)

        index = subresources.decode(id_str, resolved)
        if index >= len(member_ids):
            raise PreviewError('subresource index out of range')
        info = manifest[member_ids[index]]
        href = info['href']
        if not href:
            raise PreviewError('resource has no href')
        member = epub.resolve_href(opf_dir, href)
        if member not in reader.names():
            raise PreviewError('resource not found in archive')
        content_type = info['media_type'] or ''
        if content_type not in _SAFE_EPUB_IMAGE_TYPES:
            raise PreviewError(f'resource is not a supported preview image type: {content_type!r}')
        data = reader.read(member)
    return content_type, data


# ---------------------------------------------------------------------------
# CBZ
# ---------------------------------------------------------------------------

def cbz_preview_page_count(resolved) -> int:
    max_pages = config.limit('DOCUMENT_VIEWER_MAX_CBZ_PREVIEW_IMAGES')
    try:
        with _open_archive(resolved) as reader:
            names = reader.image_names_natural_order(documents.CBZ_IMAGE_SUFFIXES)
    except archives.ArchiveError:
        return 0
    return min(len(names), max_pages)


def cbz_preview_page(resolved, page: int) -> bytes:
    """1-based page index into the natural-ordered image list. Each page is
    decoded through the same `images.bounded_image_open()` helper the CBZ
    cover uses; a page that fails the check raises `PreviewError` (the
    caller treats it as "no such page" rather than aborting the rest of
    the preview).
    """
    max_pages = config.limit('DOCUMENT_VIEWER_MAX_CBZ_PREVIEW_IMAGES')
    if page < 1 or page > max_pages:
        raise PreviewError('page out of range')
    with _open_archive(resolved) as reader:
        names = reader.image_names_natural_order(documents.CBZ_IMAGE_SUFFIXES)
        if page > len(names):
            raise PreviewError('no such page')
        data = reader.read(names[page - 1])
    try:
        img = images.bounded_image_open(data)
    except images.ImageDecodeError as e:
        raise PreviewError(f'cannot decode page {page}') from e
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=85)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

PDF_PREVIEW_CACHE_VERSION = 1


def _pdf_cache_dir(resolved):
    """One directory per (document, mtime/size, render settings). A change
    to any of them yields a different directory, so a stale render is never
    served.
    """
    payload = repr((
        resolved.rel_path,
        resolved.mtime_ns,
        resolved.size,
        config.limit('DOCUMENT_VIEWER_PDF_RENDER_DPI'),
        config.limit('DOCUMENT_VIEWER_MAX_PDF_RENDER_DIMENSION'),
        config.limit('DOCUMENT_VIEWER_MAX_PDF_PREVIEW_PAGES'),
        PDF_PREVIEW_CACHE_VERSION,
    ))
    key = hashlib.sha256(payload.encode()).hexdigest()
    return config.cache_dir() / 'pdfpages' / key[:2] / key


def _cached_pdf_pages(cache_dir):
    try:
        return sorted(cache_dir.glob('page-*.jpg'), key=lambda p: documents.natural_sort_key(p.name))
    except OSError:
        return []


def _ensure_pdf_pages_cached(resolved) -> list:
    """Render the bounded preview pages **once** and cache them, so a
    detail page costs one `pdftoppm` run rather than one per page plus
    another just to count them. Returns the cached page files in order
    (empty if the renderer is absent or rendering failed -- in which case
    nothing is cached, so a later request retries).
    """
    config.validate_live()
    cache_dir = _pdf_cache_dir(resolved)

    cached = _cached_pdf_pages(cache_dir)
    if cached:
        return cached

    max_pages = config.limit('DOCUMENT_VIEWER_MAX_PDF_PREVIEW_PAGES')
    try:
        rendered = pdfrender.render_pdf_pages(resolved.fd, 1, max_pages)
    except pdfrender.PdfRenderError:
        return []
    if not rendered:
        return []

    cache_dir.mkdir(parents=True, exist_ok=True)
    for number, image in enumerate(rendered, start=1):
        buf = io.BytesIO()
        image.convert('RGB').save(buf, format='JPEG', quality=85)
        final = cache_dir / f'page-{number}.jpg'
        tmp = cache_dir / f'page-{number}.jpg.{os.getpid()}.tmp'
        tmp.write_bytes(buf.getvalue())
        os.replace(tmp, final)

    return _cached_pdf_pages(cache_dir)


def pdf_preview_pages(resolved) -> int:
    """How many preview pages the `view` page should offer `<img>` slots
    for. Shares one cached render with `pdf_preview_page()`. Returns 0 if
    the renderer isn't installed or rendering fails.
    """
    return len(_ensure_pdf_pages_cached(resolved))


def pdf_preview_page(resolved, page: int) -> bytes:
    max_pages = config.limit('DOCUMENT_VIEWER_MAX_PDF_PREVIEW_PAGES')
    if page < 1 or page > max_pages:
        raise PreviewError('page out of range')
    cached = _ensure_pdf_pages_cached(resolved)
    if page > len(cached):
        raise PreviewError('page unavailable')
    try:
        return cached[page - 1].read_bytes()
    except OSError as e:
        raise PreviewError(f'cannot read cached preview page {page}') from e

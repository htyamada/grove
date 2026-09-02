"""Shared EPUB container/OPF parsing, used by both `covers.py` (cover
extraction) and `previews.py` (TOC + section selection) so the XML-walking
logic for "which archive member is X" lives in one place.
"""
import posixpath

import defusedxml.ElementTree as DET

NS = {
    'container': 'urn:oasis:names:tc:opendocument:xmlns:container',
    'opf': 'http://www.idpf.org/2007/opf',
    'ncx': 'http://www.daisy.org/z3986/2005/ncx/',
    'xhtml': 'http://www.w3.org/1999/xhtml',
    'epub': 'http://www.idpf.org/2007/ops',
}


class EpubStructureError(Exception):
    pass


def open_package(reader):
    """Return `(opf_root, opf_path, opf_dir)` for the EPUB behind `reader`."""
    if 'META-INF/container.xml' not in reader.names():
        raise EpubStructureError('missing META-INF/container.xml')
    container_root = DET.fromstring(reader.read_text_xml('META-INF/container.xml'))
    rootfile = container_root.find('.//container:rootfile', NS)
    if rootfile is None:
        raise EpubStructureError('missing rootfile in container.xml')
    opf_path = rootfile.get('full-path')
    if not opf_path or opf_path not in reader.names():
        raise EpubStructureError('missing OPF package document')
    opf_root = DET.fromstring(reader.read_text_xml(opf_path))
    return opf_root, opf_path, posixpath.dirname(opf_path)


def manifest_items(opf_root) -> dict:
    """`item id -> {'href', 'media_type', 'properties': [...]}`, in document order."""
    manifest = opf_root.find('opf:manifest', NS)
    if manifest is None:
        return {}
    items = {}
    for item in manifest.findall('opf:item', NS):
        item_id = item.get('id')
        if not item_id:
            continue
        items[item_id] = {
            'href': item.get('href'),
            'media_type': item.get('media-type'),
            'properties': (item.get('properties') or '').split(),
        }
    return items


def spine_idrefs(opf_root) -> list:
    spine = opf_root.find('opf:spine', NS)
    if spine is None:
        return []
    return [ref.get('idref') for ref in spine.findall('opf:itemref', NS) if ref.get('idref')]


def spine_toc_id(opf_root):
    spine = opf_root.find('opf:spine', NS)
    return spine.get('toc') if spine is not None else None


def guide_reference_href(opf_root, ref_types) -> 'str | None':
    guide = opf_root.find('opf:guide', NS)
    if guide is None:
        return None
    for ref in guide.findall('opf:reference', NS):
        if ref.get('type') in ref_types and ref.get('href'):
            return ref.get('href')
    return None


def resolve_href(opf_dir: str, href: str) -> str:
    """Join a manifest-relative href against the OPF's own directory and
    normalize it into a plain archive member path (fragment stripped)."""
    href = href.split('#', 1)[0]
    return posixpath.normpath(posixpath.join(opf_dir, href))

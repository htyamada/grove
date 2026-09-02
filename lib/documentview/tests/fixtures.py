"""Fixture builders for EPUB/CBZ/PDF cover and preview tests."""
import io
import zipfile

from PIL import Image

_CONTAINER_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CHAPTER_XHTML = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hello.</p></body></html>
"""


def _jpeg_bytes(color, size=(40, 60)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='JPEG')
    return buf.getvalue()


def make_epub3(path, cover_color=(200, 30, 30)):
    opf = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Fixture Book</dc:title>
    <dc:identifier id="bookid">urn:uuid:fixture</dc:identifier>
  </metadata>
  <manifest>
    <item id="cover-img" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>
"""
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', _CONTAINER_XML)
        zf.writestr('OEBPS/content.opf', opf)
        zf.writestr('OEBPS/chapter1.xhtml', _CHAPTER_XHTML)
        zf.writestr('OEBPS/nav.xhtml', _CHAPTER_XHTML)
        zf.writestr('OEBPS/cover.jpg', _jpeg_bytes(cover_color))


def make_epub2(path, cover_color=(30, 200, 30)):
    opf = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Fixture Book 2</dc:title>
    <dc:identifier id="bookid">urn:uuid:fixture2</dc:identifier>
    <meta name="cover" content="cover-img"/>
  </metadata>
  <manifest>
    <item id="cover-img" href="cover.jpg" media-type="image/jpeg"/>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
  <guide>
    <reference type="cover" href="cover.jpg" title="Cover"/>
  </guide>
</package>
"""
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', _CONTAINER_XML)
        zf.writestr('OEBPS/content.opf', opf)
        zf.writestr('OEBPS/chapter1.xhtml', _CHAPTER_XHTML)
        zf.writestr('OEBPS/cover.jpg', _jpeg_bytes(cover_color))


def make_epub_with_svg_image(path):
    """An EPUB whose chapter embeds an <img> pointing at a manifest item
    declared as image/svg+xml, containing an embedded <script> -- used to
    verify the preview pipeline never serves it as a preview subresource.
    """
    opf = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>SVG Fixture</dc:title>
    <dc:identifier id="bookid">urn:uuid:svgfixture</dc:identifier>
  </metadata>
  <manifest>
    <item id="evil-svg" href="evil.svg" media-type="image/svg+xml"/>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>
"""
    chapter = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="evil.svg"/></body></html>
"""
    svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', _CONTAINER_XML)
        zf.writestr('OEBPS/content.opf', opf)
        zf.writestr('OEBPS/chapter1.xhtml', chapter)
        zf.writestr('OEBPS/evil.svg', svg_bytes)


def make_epub_nested_layout(path):
    """An EPUB whose content documents live in a subdirectory of the OPF's
    own directory (`OEBPS/Text/`), with an image nested one level further
    (`OEBPS/Text/Images/`). Every relative reference inside a content or
    nav document is written relative to *that document's own location*,
    per the EPUB spec -- e.g. the chapter's `<img src="Images/cover.jpg">`
    and the nav doc's `<a href="chapter1.xhtml">` both only resolve
    correctly when resolved against their own directory (`OEBPS/Text/`),
    not the OPF's directory (`OEBPS/`). Used to catch base-directory bugs
    that resolve such references against the wrong directory.
    """
    opf = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Nested Layout Fixture</dc:title>
    <dc:identifier id="bookid">urn:uuid:nestedfixture</dc:identifier>
  </metadata>
  <manifest>
    <item id="cover-img" href="Text/Images/cover.jpg" media-type="image/jpeg"/>
    <item id="ch1" href="Text/chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>
"""
    chapter = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="Images/cover.jpg"/></body></html>
"""
    nav = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><nav><a href="chapter1.xhtml">Chapter One</a></nav></body></html>
"""
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', _CONTAINER_XML)
        zf.writestr('OEBPS/content.opf', opf)
        zf.writestr('OEBPS/Text/chapter1.xhtml', chapter)
        zf.writestr('OEBPS/Text/nav.xhtml', nav)
        zf.writestr('OEBPS/Text/Images/cover.jpg', _jpeg_bytes((5, 5, 5)))


def make_epub_no_cover(path):
    opf = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>No Cover</dc:title>
    <dc:identifier id="bookid">urn:uuid:nocover</dc:identifier>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
  </spine>
</package>
"""
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', _CONTAINER_XML)
        zf.writestr('OEBPS/content.opf', opf)
        zf.writestr('OEBPS/chapter1.xhtml', _CHAPTER_XHTML)


def make_cbz(path, page_colors=((10, 10, 200), (10, 200, 10), (200, 10, 10))):
    with zipfile.ZipFile(path, 'w') as zf:
        for i, color in enumerate(page_colors, start=1):
            zf.writestr(f'page{i:03d}.jpg', _jpeg_bytes(color, size=(60, 90)))


def make_pdf(path, size=(80, 120), color=(50, 100, 150)):
    img = Image.new('RGB', size, color)
    img.save(path, format='PDF')


def make_encrypted_zip(path):
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('page001.jpg', _jpeg_bytes((1, 2, 3)))
        # zipfile can't natively write AES/PKZIP-encrypted entries; flip the
        # standard "encrypted" flag bit directly so the reader's detection
        # (which only inspects flag_bits, never decrypts) is exercised.
        zf.NameToInfo['page001.jpg'].flag_bits |= 0x1


def make_malformed_zip(path):
    with open(path, 'wb') as f:
        f.write(b'not actually a zip file' * 4)

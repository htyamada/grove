"""Shared bounded-image decode helper.

Used by every place in `documentview` that decodes an arbitrary image byte
stream -- `covers.py`'s EPUB/CBZ/PDF-raster cover extraction and
`previews.py`'s CBZ page decoding alike -- so the decompression-bomb policy
lives in exactly one place instead of being restated (and potentially
drifting) per call site.

Deliberately does not touch the process-global `PIL.Image.MAX_IMAGE_PIXELS`:
`documentview` shares its process with other Pillow consumers, so a
module-level assignment there would be a global side effect. Instead,
`Image.open()` only reads the header, so `img.size` is checked against
`DOCUMENT_VIEWER_MAX_IMAGE_PIXELS` before `.load()` is ever called.
"""
import warnings
from io import BytesIO

from PIL import Image

from . import config


class ImageDecodeError(Exception):
    pass


def bounded_image_open(data: bytes) -> Image.Image:
    max_pixels = config.limit('DOCUMENT_VIEWER_MAX_IMAGE_PIXELS')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            img = Image.open(BytesIO(data))
            width, height = img.size
            if width * height > max_pixels:
                raise ImageDecodeError(f'image exceeds pixel limit: {width}x{height}')
            img.load()
    except ImageDecodeError:
        raise
    except Exception as e:
        raise ImageDecodeError(f'cannot decode image: {e}') from e
    return img

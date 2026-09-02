"""Shared, sandboxed PDF page rendering via `pdftoppm` (poppler-utils).

Shared by `covers.py` (first-page cover extraction) and `previews.py`
(first-few-page preview), per spec 4.4. `pdftoppm` is standardized on; no
`ebooklib`/`pypdf`/PyMuPDF/`pdf2image` dependency is added, and `mutool` is
not wired up as a second code path.

The already-open, already-validated file descriptor from
`paths.resolve_document()` is passed to the child via `/proc/self/fd/<fd>`
with `pass_fds`, so the wrapper never reopens the original pathname. The
subprocess runs with a fixed argv list (`shell=False`), in a private
`tempfile.mkdtemp()` under `DOCUMENT_VIEWER_CACHE_DIR`, capped at
`DOCUMENT_VIEWER_PDF_RENDER_DPI` / `DOCUMENT_VIEWER_MAX_PDF_RENDER_DIMENSION`
(via Poppler's own `-scale-to`, so an enormous declared page size cannot
produce an enormous raster), and started with `start_new_session=True` so a
`DOCUMENT_VIEWER_SUBPROCESS_TIMEOUT` timeout can kill the whole process
group, poppler's own children included. The private temp directory and
every generated output are removed in a `finally` path after success,
failure, or timeout -- no response ever streams from a path cleanup has
already removed.
"""
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from . import config, documents, images

_PDFTOPPM = shutil.which('pdftoppm')


class PdfRenderError(Exception):
    pass


def is_available() -> bool:
    return _PDFTOPPM is not None


def render_pdf_pages(fd: int, first_page: int, last_page: int) -> list:
    """Render pages `[first_page, last_page]` (1-based, inclusive) of the
    PDF behind the already-open, already-validated `fd`. Returns a list of
    decoded, bounded-checked `PIL.Image` objects (possibly fewer than
    requested if some pages fail to decode), or `[]` if `pdftoppm` is not
    installed.
    """
    if _PDFTOPPM is None:
        return []

    dpi = config.limit('DOCUMENT_VIEWER_PDF_RENDER_DPI')
    max_dim = config.limit('DOCUMENT_VIEWER_MAX_PDF_RENDER_DIMENSION')
    timeout = config.limit('DOCUMENT_VIEWER_SUBPROCESS_TIMEOUT')

    config.validate_live()
    tmp_dir = Path(tempfile.mkdtemp(dir=config.cache_dir(), prefix='pdfrender-'))
    try:
        out_prefix = tmp_dir / 'page'
        argv = [
            _PDFTOPPM,
            '-jpeg',
            '-r', str(dpi),
            '-f', str(first_page),
            '-l', str(last_page),
            '-scale-to', str(max_dim),
            f'/proc/self/fd/{fd}',
            str(out_prefix),
        ]
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
            pass_fds=(fd,),
        )
        try:
            _, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.communicate()
            raise PdfRenderError('pdftoppm timed out') from None

        if proc.returncode != 0:
            raise PdfRenderError(
                f'pdftoppm failed (rc={proc.returncode}): {stderr.decode(errors="replace")[:400]}'
            )

        results = []
        for path in sorted(tmp_dir.glob('page*.jpg'), key=lambda p: documents.natural_sort_key(p.name)):
            try:
                data = path.read_bytes()
                img = images.bounded_image_open(data)
            except (OSError, images.ImageDecodeError):
                continue
            results.append(img.copy())
        return results
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

"""Shared fixtures for imhandler Step 2 enforcement/CLI tests.

Private-helper naming, matching tests/_hty7_install_check.py: this module is
not itself a test module, it is imported by test_imhandler_enforcement.py
and test_imhandler_cli_blacklist.py.
"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'lib'
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from imhandler import appconfig


def write_image(path: Path, size: tuple[int, int] = (20, 20), color: str = 'red') -> Path:
    """Write a real, tiny JPEG at path so PIL (thumbnailing, embedding) can
    actually open it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', size, color=color).save(path, 'JPEG')
    return path


def tree_fingerprint(root: Path) -> dict:
    """Return {relpath: (size, st_mtime_ns, st_mode, sha256)} for every file
    under root. Used to assert an archive is byte-for-byte unchanged after a
    mutating command runs (section 2.3: "tests must assert that the source
    archive is unchanged")."""
    fp: dict = {}
    for p in sorted(Path(root).rglob('*')):
        if p.is_file():
            st = p.stat()
            fp[str(p.relative_to(root))] = (
                st.st_size, st.st_mtime_ns, st.st_mode,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
    return fp


class ImhandlerFixtureTestCase(unittest.TestCase):
    """Base test case: a temp two-root archive with patched appconfig, no
    real Django or manage.py dependency (same mocking pattern as
    tests/test_appconfig.py and tests/test_imhandler_blacklist.py)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root1 = base / 'root1'
        self.root2 = base / 'root2'
        self.root1.mkdir()
        self.root2.mkdir()
        self.cache_dir = base / 'cache'

        self._patches = [
            mock.patch.object(appconfig, 'image_roots', [str(self.root1), str(self.root2)]),
            mock.patch.object(appconfig, 'image_root_names', ['root1', 'root2']),
            mock.patch.object(appconfig, 'cache_dir', str(self.cache_dir)),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

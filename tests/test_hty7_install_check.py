import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hty7_install_check import _installed_hty7_root, check_hty7_install_freshness


def _write(path: Path, content: str = 'x = 1\n') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class Hty7InstallFreshnessTests(unittest.TestCase):
    def test_missing_source_tree_is_a_no_op(self) -> None:
        # No machine actually has hty7 checked out here; nothing to compare.
        check_hty7_install_freshness(source_root=Path('/nonexistent/hty7'))

    def test_uninstalled_hty7_is_a_no_op(self) -> None:
        """If hty7 isn't importable at all, this check must stay silent
        and let the real ImportError surface on its own rather than
        obscuring it with a freshness message."""
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / 'source'
            _write(source_root / 'mod.py')
            with mock.patch(
                '_hty7_install_check._installed_hty7_root', return_value=None,
            ):
                check_hty7_install_freshness(source_root=source_root)

    def test_fresh_install_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / 'source'
            installed_root = Path(tmp) / 'installed'
            _write(source_root / 'a.py')
            _write(source_root / 'sub' / 'b.py')
            _write(installed_root / 'a.py')
            _write(installed_root / 'sub' / 'b.py')
            # rsync -a (make install-libraries) preserves source mtimes;
            # simulate that exactly, rather than "happens to be newer".
            for rel in ('a.py', 'sub/b.py'):
                st = (source_root / rel).stat()
                os.utime(installed_root / rel, (st.st_atime, st.st_mtime))
            with mock.patch(
                '_hty7_install_check._installed_hty7_root',
                return_value=installed_root,
            ):
                check_hty7_install_freshness(source_root=source_root)

    def test_source_newer_than_installed_raises_with_an_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / 'source'
            installed_root = Path(tmp) / 'installed'
            _write(installed_root / 'a.py')
            time.sleep(0.01)
            _write(source_root / 'a.py')  # edited after the last install
            with mock.patch(
                '_hty7_install_check._installed_hty7_root',
                return_value=installed_root,
            ):
                with self.assertRaises(RuntimeError) as exc:
                    check_hty7_install_freshness(source_root=source_root)
        message = str(exc.exception)
        self.assertIn('a.py', message)
        self.assertIn('make install-libraries', message)

    def test_file_missing_from_installed_copy_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / 'source'
            installed_root = Path(tmp) / 'installed'
            _write(source_root / 'new_module.py')
            installed_root.mkdir(parents=True)
            with mock.patch(
                '_hty7_install_check._installed_hty7_root',
                return_value=installed_root,
            ):
                with self.assertRaises(RuntimeError) as exc:
                    check_hty7_install_freshness(source_root=source_root)
        self.assertIn('new_module.py', str(exc.exception))
        self.assertIn('missing from the installed copy', str(exc.exception))

    def test_pycache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / 'source'
            installed_root = Path(tmp) / 'installed'
            _write(source_root / '__pycache__' / 'a.cpython-312.pyc', 'not python')
            installed_root.mkdir(parents=True)
            with mock.patch(
                '_hty7_install_check._installed_hty7_root',
                return_value=installed_root,
            ):
                # __pycache__ holds no .py files, so rglob('*.py') never
                # sees it regardless of the explicit skip; this just locks
                # in that a bytecode-cache directory can never trigger a
                # false positive.
                check_hty7_install_freshness(source_root=source_root)

    def test_already_pointed_at_the_source_tree_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / 'source'
            source_root.mkdir()
            with mock.patch(
                '_hty7_install_check._installed_hty7_root',
                return_value=source_root,
            ):
                check_hty7_install_freshness(source_root=source_root)

    def test_the_real_installed_root_matches_the_real_hty7_module(self) -> None:
        """Sanity check for _installed_hty7_root() itself, against the
        genuinely installed hty7 on this machine (not a fabricated one)."""
        import hty7

        self.assertEqual(
            _installed_hty7_root(), Path(hty7.__file__).resolve().parent,
        )


if __name__ == '__main__':
    unittest.main()

import os

from .. import paths
from .base import DocumentViewTestCase


class ResolveDirectoryTests(DocumentViewTestCase):
    def test_resolves_root(self):
        resolved = paths.resolve_directory('')
        self.assertEqual(resolved.rel_path, '')
        self.assertEqual(resolved.abs_path, self.root)

    def test_resolves_nested_directory(self):
        self.mkdir('a/b')
        resolved = paths.resolve_directory('a/b')
        self.assertEqual(resolved.rel_path, 'a/b')
        self.assertEqual(resolved.abs_path, self.root / 'a' / 'b')

    def test_missing_directory_not_found(self):
        with self.assertRaises(paths.NotFoundError):
            paths.resolve_directory('nope')

    def test_rejects_absolute_input(self):
        with self.assertRaises(paths.PathError):
            paths.resolve_directory('/etc')

    def test_rejects_traversal(self):
        self.mkdir('a')
        with self.assertRaises(paths.PathError):
            paths.resolve_directory('a/../../etc')

    def test_rejects_nul_byte(self):
        with self.assertRaises(paths.PathError):
            paths.resolve_directory('a\x00b')

    def test_missing_root_raises(self):
        import shutil

        shutil.rmtree(self.root)
        with self.assertRaises(Exception):
            paths.resolve_directory('')

    def test_permission_error_on_directory(self):
        self.mkdir('locked/inner')
        (self.root / 'locked').chmod(0o000)
        try:
            with self.assertRaises(paths.NotFoundError):
                paths.resolve_directory('locked/inner')
        finally:
            (self.root / 'locked').chmod(0o755)

    def test_directory_position_symlink_is_hidden(self):
        # The confirmed collection shape never has a directory-position
        # symlink, but resolve_directory() must still fail closed if one
        # appeared instead of following it.
        self.mkdir('real')
        (self.root / 'fakedir').symlink_to(self.root / 'real', target_is_directory=True)
        with self.assertRaises(paths.NotFoundError):
            paths.resolve_directory('fakedir')


class ResolveDocumentTests(DocumentViewTestCase):
    def test_resolves_supported_file(self):
        self.touch('a/Book.epub')
        with paths.resolve_document('a/Book.epub') as doc:
            self.assertEqual(doc.rel_path, 'a/Book.epub')
            self.assertEqual(doc.suffix, 'epub')
            self.assertGreater(doc.size, 0)
            self.assertEqual(os.read(doc.fd, 10), b'x')

    def test_missing_file_not_found(self):
        with self.assertRaises(paths.NotFoundError):
            paths.resolve_document('nope.epub')

    def test_unsupported_suffix_rejected(self):
        self.touch('a.exe')
        with self.assertRaises(paths.PathError):
            paths.resolve_document('a.exe')

    def test_directory_is_not_a_document(self):
        self.mkdir('a')
        with self.assertRaises(paths.PathError):
            paths.resolve_document('a')

    def test_rejects_absolute_input(self):
        with self.assertRaises(paths.PathError):
            paths.resolve_document('/etc/passwd')

    def test_rejects_traversal(self):
        self.touch('a.txt')
        with self.assertRaises(paths.PathError):
            paths.resolve_document('../a.txt')

    def test_rejects_nul_byte(self):
        with self.assertRaises(paths.PathError):
            paths.resolve_document('a\x00.txt')

    def test_permission_error_on_file(self):
        p = self.touch('locked.txt')
        p.chmod(0o000)
        try:
            with self.assertRaises(paths.PathError):
                paths.resolve_document('locked.txt')
        finally:
            p.chmod(0o644)

    def test_in_hierarchy_symlink_is_followed(self):
        target = self.touch('real/Book.pdf', b'pdfbytes')
        (self.root / 'link').mkdir()
        (self.root / 'link' / 'Book.pdf').symlink_to(target)
        with paths.resolve_document('link/Book.pdf') as doc:
            self.assertEqual(doc.suffix, 'pdf')
            self.assertEqual(os.read(doc.fd, 20), b'pdfbytes')

    def test_symlink_escaping_root_is_hidden(self):
        import tempfile

        outside = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
        outside.write(b'outside')
        outside.close()
        try:
            (self.root / 'escape.pdf').symlink_to(outside.name)
            with self.assertRaises(paths.NotFoundError):
                paths.resolve_document('escape.pdf')
        finally:
            os.unlink(outside.name)

    def test_symlink_chain_past_max_hops_rejected(self):
        target = self.touch('final.txt', b'end')
        cur = target
        # DOCUMENT_VIEWER_MAX_SYMLINK_HOPS defaults to 2; build a chain of 4
        # hops, which must be rejected regardless of where the limit is set.
        for i in range(4):
            link = self.root / f'hop{i}.txt'
            link.symlink_to(cur)
            cur = link
        with self.assertRaises(paths.NotFoundError):
            paths.resolve_document(cur.name)

    def test_final_component_toctou_swap_fails_closed(self):
        # After resolution has validated a document and pinned its real
        # parent directory, replace the final component with a symlink
        # pointing outside the root. The already-open descriptor from the
        # first resolution is unaffected; a *fresh* resolution of the same
        # name must see the swap and fail closed rather than following it.
        import tempfile

        self.touch('swap.txt', b'original')
        with paths.resolve_document('swap.txt') as first:
            self.assertEqual(os.read(first.fd, 20), b'original')

            outside = tempfile.NamedTemporaryFile(suffix='.txt', delete=False)
            outside.write(b'attacker')
            outside.close()
            try:
                (self.root / 'swap.txt').unlink()
                (self.root / 'swap.txt').symlink_to(outside.name)
                with self.assertRaises(paths.NotFoundError):
                    paths.resolve_document('swap.txt')
            finally:
                os.unlink(outside.name)

    def test_representative_reopen_reads_correct_bytes(self):
        # Confirms the final O_NOFOLLOW open reads from the validated real
        # file, not a stale path string.
        self.touch('a/Book.txt', b'hello world')
        with paths.resolve_document('a/Book.txt') as doc:
            self.assertEqual(os.read(doc.fd, 100), b'hello world')

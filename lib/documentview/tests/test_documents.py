from .. import documents
from .base import DocumentViewTestCase


class ScanDirectoryTests(DocumentViewTestCase):
    def _scan(self, rel=''):
        abs_dir = self.root / rel if rel else self.root
        return documents.scan_directory(abs_dir, rel)

    def test_epub_pdf_same_basename_group_as_one_document(self):
        self.touch('a/Some Book.epub')
        self.touch('a/Some Book.pdf')
        subdirs, docs = self._scan('a')
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].basename, 'Some Book')
        self.assertEqual(set(docs[0].variants), {'epub', 'pdf'})

    def test_same_basename_in_different_directory_stays_distinct(self):
        self.touch('a/Book.epub')
        self.touch('b/Book.epub')
        _, docs_a = self._scan('a')
        _, docs_b = self._scan('b')
        self.assertEqual(len(docs_a), 1)
        self.assertEqual(len(docs_b), 1)
        self.assertNotEqual(docs_a[0].variants['epub'].rel_path, docs_b[0].variants['epub'].rel_path)

    def test_every_variant_independently_addressable(self):
        self.touch('a/Book.epub')
        self.touch('a/Book.pdf')
        _, docs = self._scan('a')
        doc = docs[0]
        self.assertEqual(doc.variants['epub'].rel_path, 'a/Book.epub')
        self.assertEqual(doc.variants['pdf'].rel_path, 'a/Book.pdf')

    def test_unsupported_suffix_not_a_variant(self):
        self.touch('a/Book.epub')
        self.touch('a/Book.exe')
        self.touch('a/readme.txt.bak')
        _, docs = self._scan('a')
        self.assertEqual(len(docs), 1)
        self.assertEqual(set(docs[0].variants), {'epub'})

    def test_tar_pdf_basename_keeps_dot_tar(self):
        self.touch('a/Book.tar.pdf')
        _, docs = self._scan('a')
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].basename, 'Book.tar')

    def test_duplicate_normalized_format_is_collection_error(self):
        self.touch('a/Book.pdf')
        self.touch('a/Book.PDF')
        _, docs = self._scan('a')
        self.assertEqual(len(docs), 1)
        doc = docs[0]
        self.assertEqual(len(doc.variants), 1)
        self.assertTrue(doc.errors)

    def test_duplicate_normalized_format_names_both_files_in_the_error(self):
        self.touch('a/Book.pdf')
        self.touch('a/Book.PDF')
        _, docs = self._scan('a')
        error = ' '.join(docs[0].errors)
        self.assertIn('Book.pdf', error)
        self.assertIn('Book.PDF', error)

    def test_duplicate_normalized_format_resolves_deterministically(self):
        # os.scandir yields in arbitrary filesystem order; the variant kept
        # for download/activation must not depend on it. Rescanning must
        # always pick the same file.
        self.touch('a/Book.pdf')
        self.touch('a/Book.PDF')
        chosen = {self._scan('a')[1][0].variants['pdf'].filename for _ in range(5)}
        self.assertEqual(len(chosen), 1)
        # Sorted by name, so the uppercase suffix sorts first.
        self.assertEqual(chosen.pop(), 'Book.PDF')

    def test_case_sensitive_basenames_do_not_group(self):
        self.touch('a/Book.epub')
        self.touch('a/book.pdf')
        _, docs = self._scan('a')
        self.assertEqual(len(docs), 2)

    def test_dotfiles_hidden(self):
        self.touch('a/.hidden.epub')
        _, docs = self._scan('a')
        self.assertEqual(docs, [])

    def test_stable_natural_ordering_with_tie_break(self):
        for name in ('img10.txt', 'img2.txt', 'img1.txt'):
            self.touch(f'a/{name}')
        _, docs = self._scan('a')
        self.assertEqual([d.basename for d in docs], ['img1', 'img2', 'img10'])

    def test_subdirs_and_documents_returned_separately(self):
        self.mkdir('a/sub')
        self.touch('a/Book.epub')
        subdirs, docs = self._scan('a')
        self.assertEqual([d.name for d in subdirs], ['sub'])
        self.assertEqual([d.basename for d in docs], ['Book'])


class RepresentativeVariantTests(DocumentViewTestCase):
    def test_prefers_epub_over_pdf(self):
        self.touch('a/Book.epub')
        self.touch('a/Book.pdf')
        _, docs = documents.scan_directory(self.root / 'a', 'a')
        rep = documents.representative_variant(docs[0])
        self.assertEqual(rep.suffix, 'epub')

    def test_falls_back_through_preference_order(self):
        self.touch('a/Book.txt')
        self.touch('a/Book.cbz')
        _, docs = documents.scan_directory(self.root / 'a', 'a')
        rep = documents.representative_variant(docs[0])
        self.assertEqual(rep.suffix, 'cbz')


class NaturalSortKeyTests(DocumentViewTestCase):
    def test_numeric_ordering(self):
        names = ['10.jpg', '2.jpg', '1.jpg']
        self.assertEqual(
            sorted(names, key=documents.natural_sort_key),
            ['1.jpg', '2.jpg', '10.jpg'],
        )

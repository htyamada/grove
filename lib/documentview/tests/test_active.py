import threading

from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from .. import active, documents, paths, views
from .base import DocumentViewTestCase
from .test_download import DocumentViewClientTestCase


class AddActiveTests(DocumentViewTestCase):
    def test_copies_bytes_exactly(self):
        source = self.touch('a/Book.epub', b'exact original bytes')
        active.add_active('a/Book.epub')
        self.assertEqual((self.active / 'Book.epub').read_bytes(), source.read_bytes())

    def test_add_writes_a_regular_file_not_a_link(self):
        self.touch('a/Book.epub')
        active.add_active('a/Book.epub')
        self.assertTrue((self.active / 'Book.epub').is_file())
        self.assertFalse((self.active / 'Book.epub').is_symlink())

    def test_editing_the_source_afterward_does_not_change_the_copy(self):
        source = self.touch('a/Book.epub', b'version one')
        active.add_active('a/Book.epub')
        source.write_bytes(b'version two, different length')
        self.assertEqual((self.active / 'Book.epub').read_bytes(), b'version one')

    def test_repeated_add_of_unchanged_source_produces_one_consistent_copy(self):
        self.touch('a/Book.epub')
        name1 = active.add_active('a/Book.epub')
        name2 = active.add_active('a/Book.epub')
        self.assertEqual(name1, name2)
        self.assertEqual(len(list(self.active.iterdir())), 1)

    def test_readd_after_source_changes_updates_the_copy(self):
        source = self.touch('a/Book.epub', b'old content')
        active.add_active('a/Book.epub')
        source.write_bytes(b'new content, different size')

        active.add_active('a/Book.epub')
        self.assertEqual((self.active / 'Book.epub').read_bytes(), b'new content, different size')

    def test_add_overwrites_a_pre_existing_file_at_the_computed_name(self):
        # Directory-is-authority: any visible file occupying the name is
        # treated as managed and simply replaced, whether this app wrote it
        # or not.
        self.touch('a/Book.epub', b'source content')
        self.active.mkdir(parents=True, exist_ok=True)
        (self.active / 'Book.epub').write_bytes(b'unrelated pre-existing content')

        active.add_active('a/Book.epub')

        self.assertEqual((self.active / 'Book.epub').read_bytes(), b'source content')

    def test_same_filename_different_directory_replaces_copy(self):
        self.touch('a/Book.epub', b'from a')
        self.touch('b/Book.epub', b'from b')
        active.add_active('a/Book.epub')
        active.add_active('b/Book.epub')

        self.assertEqual((self.active / 'Book.epub').read_bytes(), b'from b')
        self.assertEqual(len(list(self.active.iterdir())), 1)

    def test_activating_one_variant_leaves_siblings_untouched(self):
        self.touch('a/Book.epub')
        self.touch('a/Book.pdf')
        active.add_active('a/Book.epub')
        self.assertEqual(len(list(self.active.iterdir())), 1)
        self.assertTrue((self.active / 'Book.epub').exists())
        self.assertFalse((self.active / 'Book.pdf').exists())

    def test_add_active_rejects_invalid_source(self):
        with self.assertRaises(paths.PathError):
            active.add_active('../etc/passwd')


class RemoveActiveTests(DocumentViewTestCase):
    def test_remove_valid_export(self):
        self.touch('a/Book.epub')
        active.add_active('a/Book.epub')
        result = active.remove_active('Book.epub')
        self.assertEqual(result.link_name, 'Book.epub')
        self.assertFalse((self.active / 'Book.epub').exists())

    def test_remove_refuses_unregistered_name(self):
        self.active.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(active.ActiveError):
            active.remove_active('not-there.epub')

    def test_remove_succeeds_for_a_hand_placed_file(self):
        # Directory-is-authority: presence as a file in the exports
        # directory is the only authorization needed, whether or not the
        # app itself wrote it there.
        self.active.mkdir(parents=True, exist_ok=True)
        (self.active / 'HandPlaced.epub').write_bytes(b'not written by this app')

        result = active.remove_active('HandPlaced.epub')
        self.assertEqual(result.link_name, 'HandPlaced.epub')
        self.assertFalse((self.active / 'HandPlaced.epub').exists())

    def test_remove_refuses_a_directory_entry(self):
        self.active.mkdir(parents=True, exist_ok=True)
        (self.active / 'Book.epub').mkdir()
        with self.assertRaises(active.ActiveError):
            active.remove_active('Book.epub')
        self.assertTrue((self.active / 'Book.epub').is_dir())

    def test_remove_cannot_smuggle_arbitrary_path(self):
        with self.assertRaises(active.ActiveError):
            active.remove_active('../etc/passwd')
        with self.assertRaises(active.ActiveError):
            active.remove_active('sub/dir/name')

    def test_remove_succeeds_even_after_source_is_deleted(self):
        # The exported copy is independent of the source's lifecycle --
        # removal never inspects or needs the source at all.
        source = self.touch('a/Book.epub')
        active.add_active('a/Book.epub')
        source.unlink()

        result = active.remove_active('Book.epub')
        self.assertEqual(result.link_name, 'Book.epub')
        self.assertFalse((self.active / 'Book.epub').exists())

    def test_remove_does_not_touch_the_source_file(self):
        source = self.touch('a/Book.epub', b'original content')
        active.add_active('a/Book.epub')
        active.remove_active('Book.epub')
        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), b'original content')


class ConcurrencyTests(DocumentViewTestCase):
    def test_concurrent_add_same_source_stays_consistent(self):
        source = self.touch('a/Book.epub', b'concurrent content')
        errors = []

        def worker():
            try:
                active.add_active('a/Book.epub')
            except active.ActiveError as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertTrue((self.active / 'Book.epub').is_file())
        self.assertEqual(len(list(self.active.iterdir())), 1)
        self.assertEqual((self.active / 'Book.epub').read_bytes(), source.read_bytes())

    def test_concurrent_add_then_remove_leaves_consistent_state(self):
        self.touch('a/Book.epub')
        active.add_active('a/Book.epub')
        results = []

        def remover():
            try:
                results.append(active.remove_active('Book.epub'))
            except active.ActiveError as e:
                results.append(e)

        threads = [threading.Thread(target=remover) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one remove can succeed; the rest see a clean "not
        # present" error. Either way, the directory ends up consistent.
        successes = [r for r in results if isinstance(r, active.RemoveResult)]
        self.assertEqual(len(successes), 1)
        self.assertFalse((self.active / 'Book.epub').exists())


class ActiveHttpTests(DocumentViewClientTestCase):
    def test_add_hides_the_add_control_and_shows_a_notice(self):
        self.touch('a/Book.epub')
        r = self.post('/documents/active/add/', {'rel_path': 'a/Book.epub'})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b'Add to exports', r.content)
        self.assertIn(b'Added', r.content)
        self.assertIn(b'Exports', r.content)

    def test_active_add_requires_mutate_authorization(self):
        from django.test import Client

        self.touch('a/Book.epub')
        anon = Client()
        r = anon.post('/documents/active/add/', {'rel_path': 'a/Book.epub'}, HTTP_HOST='localhost')
        self.assertEqual(r.status_code, 403)

    def test_active_add_get_not_allowed(self):
        self.touch('a/Book.epub')
        r = self.get('/documents/active/add/?rel_path=a/Book.epub')
        self.assertEqual(r.status_code, 405)

    def test_browse_shows_export_badge_after_activation(self):
        self.touch('a/Book.epub')
        active.add_active('a/Book.epub')
        r = self.get('/documents/browse/a/')
        self.assertIn(b'exported', r.content)

    def test_browse_offers_add_control_only_for_non_exported_formats(self):
        self.touch('a/Book.epub')
        self.touch('a/Book.pdf')
        active.add_active('a/Book.epub')
        for mode in ('cover', 'title'):
            r = self.get(f'/documents/browse/a/?view={mode}')
            self.assertNotContains(r, 'Add EPUB to exports')
            self.assertContains(r, 'Add PDF to exports')

    def test_browse_never_offers_a_remove_control(self):
        self.touch('a/Book.epub')
        active.add_active('a/Book.epub')
        r = self.get('/documents/browse/a/')
        self.assertNotIn(b'Remove from exports', r.content)

    def test_view_page_never_offers_a_remove_control(self):
        self.touch('a/Book.epub')
        active.add_active('a/Book.epub')
        r = self.get('/documents/view/a/Book.epub/')
        self.assertNotIn(b'Remove from exports', r.content)
        self.assertNotIn(b'Add to exports', r.content)  # already exported

    def test_add_from_browse_returns_to_the_listing(self):
        self.touch('a/Book.epub')
        listing = '/documents/browse/a/?view=cover'

        added = self.post('/documents/active/add/', {'rel_path': 'a/Book.epub', 'return_to': listing})
        self.assertRedirects(added, listing, fetch_redirect_response=False)
        r = self.get(listing)
        self.assertContains(r, 'exported')

    def test_mutation_does_not_redirect_to_an_external_host(self):
        self.touch('a/Book.epub')
        r = self.post(
            '/documents/active/add/',
            {'rel_path': 'a/Book.epub', 'return_to': 'https://example.com/'},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Added', r.content)

    def test_remove_succeeds_even_when_the_source_is_missing(self):
        # Regression: the endpoint must operate on link_name alone -- the
        # exported copy is independent of the source's current state.
        source = self.touch('a/Book.epub')
        active.add_active('a/Book.epub')
        source.unlink()

        r = self.post('/documents/active/remove/', {'link_name': 'Book.epub'})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Removed', r.content)
        self.assertFalse((self.active / 'Book.epub').exists())

    def test_remove_of_an_absent_name_reports_an_error(self):
        r = self.post('/documents/active/remove/', {'link_name': 'NoSuchFile.epub'})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'not present', r.content)


class SymlinkedSourceActiveStateTests(DocumentViewTestCase):
    """Regression: the real collection curates books by symlinking them
    into `humble-bundle/selected/` from sibling directories. Browse lists
    such a document under the *symlink's* path, while `add_active()`
    resolves through the symlink and copies under the real target's
    filename. Badge lookup must still recognize the document as exported
    under both the curated path and its real path, since both share the
    same filename -- no separate alias bookkeeping needed.
    """

    def _selected_layout(self):
        target = self.touch('real/Book.epub', b'epub-bytes')
        self.mkdir('selected')
        (self.root / 'selected' / 'Book.epub').symlink_to(target)
        return target

    def test_activating_via_symlink_badges_both_paths(self):
        self._selected_layout()
        active.add_active('selected/Book.epub')

        exported = active.exported_names()
        self.assertIn('Book.epub', exported)

        _, selected_docs = documents.scan_directory(self.root / 'selected', 'selected')
        self.assertIn(selected_docs[0].variants['epub'].filename, exported)

        _, real_docs = documents.scan_directory(self.root / 'real', 'real')
        self.assertIn(real_docs[0].variants['epub'].filename, exported)

    def test_activating_target_directly_is_the_same_export(self):
        # Same underlying file, reached two ways -- must collapse to one
        # idempotent copy, not collide.
        self._selected_layout()
        first = active.add_active('selected/Book.epub')
        second = active.add_active('real/Book.epub')
        self.assertEqual(first, second)
        self.assertEqual(len(list(self.active.iterdir())), 1)

    def test_view_page_offers_no_remove_control_for_a_symlinked_document(self):
        self._selected_layout()
        active.add_active('selected/Book.epub')
        _, docs = documents.scan_directory(self.root / 'selected', 'selected')
        rows = views._variant_rows(docs[0])
        self.assertTrue(rows[0]['exported'])


class SymlinkedSourceLogicalGroupingTests(DocumentViewClientTestCase):
    """Regression: a document reached through an in-hierarchy symlink must
    regroup with the *other files actually listed alongside it* (the
    symlink's own directory), not with whatever happens to share a
    basename in the symlink target's real directory. Otherwise
    `selected/Alias.epub -> real/Canonical.epub` sitting next to
    `selected/Alias.pdf` shows one "Alias" document with both formats when
    browsed, but its own detail page turns into "Canonical" with only the
    EPUB variant.
    """

    def _aliased_layout(self):
        self.touch('real/Canonical.epub', b'epub-bytes')
        self.mkdir('selected')
        (self.root / 'selected' / 'Alias.epub').symlink_to(self.root / 'real' / 'Canonical.epub')
        self.touch('selected/Alias.pdf', b'pdf-bytes')

    def test_resolve_logical_groups_with_the_requested_directorys_sibling(self):
        self._aliased_layout()
        document, resolved = views._resolve_logical('selected/Alias.epub')
        self.assertIsNotNone(document)
        self.assertEqual(document.basename, 'Alias')
        self.assertEqual(set(document.variants), {'epub', 'pdf'})
        self.assertEqual(document.variants['epub'].rel_path, 'selected/Alias.epub')
        self.assertEqual(document.variants['pdf'].rel_path, 'selected/Alias.pdf')
        self.assertEqual(resolved.rel_path, 'selected/Alias.epub')

    def test_view_page_shows_both_variants_for_the_symlinked_alias(self):
        self._aliased_layout()
        r = self.get('/documents/view/selected/Alias.epub/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Alias.epub', r.content)
        self.assertIn(b'Alias.pdf', r.content)
        self.assertIn(b'PDF', r.content)

    def test_activating_through_the_view_exports_the_canonical_target(self):
        self._aliased_layout()
        r = self.post('/documents/active/add/', {'rel_path': 'selected/Alias.epub'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue((self.active / 'Canonical.epub').is_file())
        self.assertEqual((self.active / 'Canonical.epub').read_bytes(), b'epub-bytes')


class ExportsPageTests(DocumentViewClientTestCase):
    def test_lists_valid_export(self):
        self.touch('a/Book.epub')
        active.add_active('a/Book.epub')
        r = self.get('/documents/exports/')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Book')
        self.assertContains(r, 'Remove from exports')

    def test_hidden_entries_are_never_listed(self):
        self.active.mkdir(parents=True, exist_ok=True)
        (self.active / '.DS_Store').write_bytes(b'macos metadata')
        r = self.get('/documents/exports/')
        self.assertNotContains(r, '.DS_Store')

    def test_a_hand_placed_supported_file_is_listed_too(self):
        self.active.mkdir(parents=True, exist_ok=True)
        (self.active / 'HandPlaced.txt').write_bytes(b'not written by this app')
        r = self.get('/documents/exports/')
        self.assertContains(r, 'HandPlaced')

    def test_an_unsupported_file_is_silently_skipped(self):
        self.active.mkdir(parents=True, exist_ok=True)
        (self.active / 'notes.exe').write_bytes(b'not a supported document type')
        r = self.get('/documents/exports/')
        self.assertNotContains(r, 'notes.exe')

    def test_remove_control_removes_and_returns_to_the_listing(self):
        self.touch('a/Book.epub')
        active.add_active('a/Book.epub')
        listing = '/documents/exports/'

        r = self.post('/documents/active/remove/', {'link_name': 'Book.epub', 'return_to': listing})
        self.assertRedirects(r, listing, fetch_redirect_response=False)
        self.assertFalse((self.active / 'Book.epub').exists())

    def test_exports_index_requires_browse_authorization(self):
        from django.test import Client

        anon = Client()
        r = anon.get('/documents/exports/', HTTP_HOST='localhost')
        self.assertEqual(r.status_code, 403)

    def test_exports_link_appears_on_the_collection_page(self):
        r = self.get('/documents/')
        self.assertContains(r, 'dv-exports-link')
        self.assertContains(r, '/documents/exports/')

    def test_two_exports_sharing_a_basename_are_each_individually_removable(self):
        # Regression: two exported files must not collide on a
        # rel_path-keyed lookup -- each row's "Remove" form must carry its
        # own name, not whichever one happened to be processed last.
        self.active.mkdir(parents=True, exist_ok=True)
        (self.active / 'Book.epub').write_bytes(b'epub bytes')
        (self.active / 'Book.pdf').write_bytes(b'%PDF-1.4 fake')

        r = self.get('/documents/exports/')
        self.assertContains(r, 'name="link_name" value="Book.epub"')
        self.assertContains(r, 'name="link_name" value="Book.pdf"')

        removed = self.post('/documents/active/remove/', {'link_name': 'Book.epub'})
        self.assertEqual(removed.status_code, 200)
        self.assertFalse((self.active / 'Book.epub').exists())
        self.assertTrue((self.active / 'Book.pdf').exists())

    def test_exports_index_validates_live_config_before_scanning(self):
        # Regression: _exports_context() used to scan exports_dir directly
        # without ever calling config.validate_live() -- the check
        # browse()/view() get for free via paths.resolve_*(). A missing or
        # misconfigured root would otherwise render a silently-empty
        # Exports page instead of surfacing the configuration error.
        missing_root = self.tmp / 'does-not-exist'
        with override_settings(DOCUMENT_VIEWER_ROOT=missing_root):
            with self.assertRaises(ImproperlyConfigured):
                self.get('/documents/exports/')

import hashlib
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from ._imhandler_fixture import ImhandlerFixtureTestCase, tree_fingerprint, write_image

from imhandler import appconfig, blacklist, cache
from imhandler.clusterer import cluster_images
from imhandler.db import (
    cleanup_missing_members, get_cluster_members, open_db,
)
from imhandler.embedder import embed_images, find_semantic, find_similar
from imhandler.models import ImageEntry
from imhandler.scanner import scan, scan_all
from imhandler.thumbnailer import get_or_create, prewarm, purge


class ScannerTests(ImhandlerFixtureTestCase):
    def test_blocked_image_absent_siblings_remain(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        write_image(self.root1 / 'b.jpg')
        blacklist.add(a)

        names = {img.path.name for img in scan(self.root1).all_images()}

        self.assertEqual(names, {'b.jpg'})

    def test_scan_all_filters_across_roots(self) -> None:
        write_image(self.root1 / 'a.jpg')
        b = write_image(self.root2 / 'b.jpg')
        blacklist.add(b)

        names = {img.path.name for img in scan_all().all_images()}

        self.assertEqual(names, {'a.jpg'})

    def test_explicit_empty_blocked_bypasses_store(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        blacklist.add(a)

        with mock.patch.object(blacklist, 'load', side_effect=AssertionError('load() should not be called')):
            names = {img.path.name for img in scan(self.root1, blocked=frozenset()).all_images()}

        self.assertEqual(names, {'a.jpg'})

    def test_none_blocked_calls_load_exactly_once(self) -> None:
        write_image(self.root1 / 'sub' / 'a.jpg')
        write_image(self.root1 / 'b.jpg')

        with mock.patch.object(blacklist, 'load', wraps=blacklist.load) as load_mock:
            scan(self.root1)

        load_mock.assert_called_once()

    def test_corrupt_store_raises(self) -> None:
        write_image(self.root1 / 'a.jpg')
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / 'blacklist.json').write_text('not json', encoding='utf-8')

        with self.assertRaises(blacklist.BlacklistError):
            scan(self.root1)

    def test_unconfigured_cache_dir_scan_unfiltered(self) -> None:
        write_image(self.root1 / 'a.jpg')

        with mock.patch.object(appconfig, 'cache_dir', ''):
            album = scan(self.root1)

        self.assertEqual(len(album.all_images()), 1)

    def test_symlink_alias_blocks_real_target(self) -> None:
        real = write_image(self.root1 / 'real.jpg')
        alias_dir = self.root1 / 'aliases'
        alias_dir.mkdir()
        alias = alias_dir / 'alias.jpg'
        alias.symlink_to(real)
        blacklist.add(alias)

        names = {img.path.name for img in scan(self.root1).all_images()}

        self.assertNotIn('real.jpg', names)

    def test_empty_leaf_when_all_blocked(self) -> None:
        a = write_image(self.root1 / 'leaf' / 'a.jpg')
        blacklist.add(a)

        album = scan(self.root1)

        leaf = album.find('leaf')
        self.assertIsNotNone(leaf)
        self.assertEqual(leaf.image_count(), 0)
        self.assertIsNone(album.first_leaf())

    def test_hidden_count_leaf_only_and_in_scope(self) -> None:
        leaf_blocked = write_image(self.root1 / 'leaf' / 'blocked.jpg')
        write_image(self.root1 / 'leaf' / 'visible.jpg')
        interior_blocked = write_image(self.root1 / 'interior_blocked.jpg')
        write_image(self.root1 / 'other_sub' / 'x.jpg')
        missing = self.root1 / 'leaf' / 'gone.jpg'
        other_root_entry = write_image(self.root2 / 'y.jpg')

        for p in (leaf_blocked, interior_blocked, missing, other_root_entry):
            blacklist.add(p)

        album = scan(self.root1)

        # Only leaf_blocked is counted: interior_blocked sits in root1 itself,
        # which has subdirectories and so is an interior node whose images
        # (blocked or not) are silently discarded, never assigned to
        # hidden_images; missing was never a real directory entry; the other
        # root's entry is out of scope entirely.
        self.assertEqual(album.hidden_count(), 1)


class ThumbnailerTests(ImhandlerFixtureTestCase):
    def test_get_or_create_raises_for_blocked_and_creates_no_file(self) -> None:
        img = write_image(self.root1 / 'a.jpg')
        blacklist.add(img)
        entry = ImageEntry(path=img, rel_path=Path('a.jpg'), mtime=img.stat().st_mtime)

        with self.assertRaises(blacklist.BlockedImageError):
            get_or_create(entry)

        td = cache.thumbs_dir()
        self.assertFalse(td.exists() and any(td.rglob('*.jpg')))

    def test_explicit_blocked_skips_disk_load(self) -> None:
        img = write_image(self.root1 / 'a.jpg')
        entry = ImageEntry(path=img, rel_path=Path('a.jpg'), mtime=img.stat().st_mtime)

        with mock.patch.object(blacklist, 'load', side_effect=AssertionError('load() should not be called')):
            dest = get_or_create(entry, blocked=frozenset())

        self.assertTrue(dest.exists())

    def test_prewarm_skips_blocked_generates_rest(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root1 / 'b.jpg')
        blacklist.add(a)
        entries = [
            ImageEntry(path=a, rel_path=Path('a.jpg'), mtime=a.stat().st_mtime),
            ImageEntry(path=b, rel_path=Path('b.jpg'), mtime=b.stat().st_mtime),
        ]

        prewarm(entries)

        self.assertEqual(len(list(cache.thumbs_dir().rglob('*.jpg'))), 1)

    def test_canonicalization_catches_symlink_and_traversal_bypass(self) -> None:
        real = write_image(self.root1 / 'real.jpg')
        blacklist.add(real)

        alias = self.root1 / 'alias.jpg'
        alias.symlink_to(real)
        entry_alias = ImageEntry(path=alias, rel_path=Path('alias.jpg'), mtime=real.stat().st_mtime)
        with self.assertRaises(blacklist.BlockedImageError):
            get_or_create(entry_alias)

        traversal_path = self.root1 / 'sub' / '..' / 'real.jpg'
        entry_traversal = ImageEntry(path=traversal_path, rel_path=Path('real.jpg'), mtime=real.stat().st_mtime)
        with self.assertRaises(blacklist.BlockedImageError):
            get_or_create(entry_traversal)

        self.assertFalse(cache.thumbs_dir().exists())

    def test_alias_and_target_share_one_thumbnail(self) -> None:
        real = write_image(self.root1 / 'real.jpg')
        entry = ImageEntry(path=real, rel_path=Path('real.jpg'), mtime=real.stat().st_mtime)
        dest1 = get_or_create(entry)

        alias = self.root1 / 'alias.jpg'
        alias.symlink_to(real)
        entry_alias = ImageEntry(path=alias, rel_path=Path('alias.jpg'), mtime=real.stat().st_mtime)
        dest2 = get_or_create(entry_alias)

        self.assertEqual(dest1, dest2)


class PurgeTests(ImhandlerFixtureTestCase):
    def _make_cluster(self, db, paths) -> tuple[int, list[int]]:
        ids = []
        for p in paths:
            db.execute('INSERT INTO Images (path, mtime) VALUES (?, ?)', (str(p), 0.0))
            ids.append(db.execute('SELECT last_insert_rowid()').fetchone()[0])
        db.execute('INSERT INTO Clusters (threshold_used, model_used) VALUES (?, ?)', (0.85, 'clip'))
        cluster_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        for rank, image_id in enumerate(ids):
            db.execute(
                'INSERT INTO ClusterMembership (cluster_id, image_id, quality_rank) VALUES (?, ?, ?)',
                (cluster_id, image_id, rank),
            )
        db.commit()
        return cluster_id, ids

    def _fake_thumb(self, path: Path) -> Path:
        digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
        thumb_dir = cache.thumbs_dir() / digest[:2]
        thumb_dir.mkdir(parents=True, exist_ok=True)
        dest = thumb_dir / f'{digest}-200.jpg'
        dest.write_bytes(b'fake')
        return dest

    def test_blocked_image_purged_fingerprint_unchanged_cluster_collapses(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root1 / 'b.jpg')
        self._fake_thumb(a)
        self._fake_thumb(b)
        blacklist.add(a)

        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        cluster_id, ids = self._make_cluster(db, [a.resolve(), b.resolve()])
        db.close()

        fp_before = tree_fingerprint(self.root1)
        result = purge()
        fp_after = tree_fingerprint(self.root1)

        self.assertEqual(fp_before, fp_after)
        self.assertEqual(result.db_removed, 1)
        self.assertEqual(result.clusters_collapsed, 1)
        self.assertFalse(result.thumbs_skipped)

        remaining_thumbs = {p.name for p in cache.thumbs_dir().rglob('*.jpg')}
        b_digest = hashlib.sha256(str(b.resolve()).encode()).hexdigest()
        self.assertEqual(remaining_thumbs, {f'{b_digest}-200.jpg'})

        db2 = open_db(self.cache_dir / 'db' / 'dedup.db')
        self.assertEqual(
            [r['path'] for r in db2.execute('SELECT path FROM Images').fetchall()],
            [str(b.resolve())],
        )
        self.assertEqual(db2.execute('SELECT * FROM Clusters').fetchall(), [])
        db2.close()

    def test_dry_run_reports_without_deleting(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        blacklist.add(a)
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        self._make_cluster(db, [a.resolve()])
        db.close()

        result = purge(dry_run=True)

        self.assertEqual(result.db_removed, 1)
        db2 = open_db(self.cache_dir / 'db' / 'dedup.db')
        self.assertEqual(len(db2.execute('SELECT * FROM Images').fetchall()), 1)
        db2.close()

    def test_purge_dir_scopes_db_sweep_and_skips_thumb_sweep(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        other = self.root2 / 'stale.jpg'  # never created: a stale row outside the scanned DIR
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        self._make_cluster(db, [a.resolve(), other])
        db.close()

        stale_thumb = self._fake_thumb(self.root1 / 'unrelated.jpg')

        result = purge(self.root1)

        self.assertTrue(result.thumbs_skipped)
        self.assertTrue(stale_thumb.exists())

        db2 = open_db(self.cache_dir / 'db' / 'dedup.db')
        remaining = {r['path'] for r in db2.execute('SELECT path FROM Images').fetchall()}
        self.assertEqual(remaining, {str(a.resolve()), str(other)})
        db2.close()

    def test_purge_dir_leaves_out_of_scope_cluster_member_untouched(self) -> None:
        """A cluster with one member under the scanned DIR (which goes stale)
        and one member under a different, unscanned root must be left
        alone entirely: scoped purge has no visibility into whether the
        out-of-scope member is still valid, so it must not collapse or
        delete cluster metadata that involves it."""
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root2 / 'b.jpg')  # different root, not scanned by purge(root1)
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        cluster_id, _ = self._make_cluster(db, [a.resolve(), b.resolve()])
        db.close()
        a.unlink()  # 'a' becomes stale from root1's perspective

        result = purge(self.root1)

        self.assertEqual(result.db_removed, 1)
        self.assertEqual(result.clusters_collapsed, 0)

        db2 = open_db(self.cache_dir / 'db' / 'dedup.db')
        self.assertEqual(
            [r['path'] for r in db2.execute('SELECT path FROM Images').fetchall()],
            [str(b.resolve())],
        )
        clusters_left = db2.execute('SELECT id FROM Clusters').fetchall()
        self.assertEqual([c['id'] for c in clusters_left], [cluster_id])
        remaining_members = db2.execute(
            'SELECT image_id FROM ClusterMembership WHERE cluster_id = ?', (cluster_id,)
        ).fetchall()
        self.assertEqual(len(remaining_members), 1)
        db2.close()

    def test_zero_member_cluster_counted_in_dry_run_and_real_run(self) -> None:
        """A cluster whose every member goes stale in this run contributes
        no surviving row to remaining_by_cluster at all -- it must still be
        counted as collapsed, since the empty-cluster sweep deletes it
        regardless of whether the count noticed."""
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root1 / 'b.jpg')
        blacklist.add(a)
        blacklist.add(b)
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        cluster_id, _ = self._make_cluster(db, [a.resolve(), b.resolve()])
        db.close()

        dry_result = purge(dry_run=True)
        self.assertEqual(dry_result.clusters_collapsed, 1)

        real_result = purge()
        self.assertEqual(real_result.clusters_collapsed, 1)

        db2 = open_db(self.cache_dir / 'db' / 'dedup.db')
        self.assertEqual(db2.execute('SELECT * FROM Clusters').fetchall(), [])
        db2.close()

    def test_already_empty_cluster_counted_and_removed(self) -> None:
        """A Cluster row with zero ClusterMembership rows to begin with --
        never touched by this run's staleness at all -- never appears in
        membership_rows, so it can't be seeded into remaining_by_cluster.
        The trailing empty-cluster sweep deletes it unconditionally; the
        count must include it too."""
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        db.execute('INSERT INTO Clusters (threshold_used, model_used) VALUES (?, ?)', (0.85, 'clip'))
        empty_cluster_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.commit()
        db.close()

        dry_result = purge(dry_run=True)
        self.assertEqual(dry_result.clusters_collapsed, 1)

        real_result = purge()
        self.assertEqual(real_result.clusters_collapsed, 1)

        db2 = open_db(self.cache_dir / 'db' / 'dedup.db')
        self.assertEqual(
            db2.execute('SELECT id FROM Clusters WHERE id = ?', (empty_cluster_id,)).fetchall(), [],
        )
        db2.close()

    def test_purge_all_blocked_root_keeps_source_files(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root1 / 'b.jpg')
        blacklist.add(a)
        blacklist.add(b)

        fp_before = tree_fingerprint(self.root1)
        purge()
        fp_after = tree_fingerprint(self.root1)

        self.assertEqual(fp_before, fp_after)


class EmbedderTests(ImhandlerFixtureTestCase):
    def test_blocked_image_gets_no_row(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        write_image(self.root1 / 'b.jpg')
        blacklist.add(a)
        db = open_db(self.cache_dir / 'db' / 'dedup.db')

        with mock.patch('imhandler.embedder.load_clip_model', return_value=(mock.Mock(), mock.Mock())):
            with mock.patch('imhandler.embedder._embed_clip_batch',
                             return_value=np.zeros((1, 4), dtype=np.float32)):
                processed, skipped, excluded = embed_images(self.root1, db, model='clip')

        self.assertEqual(excluded, 1)
        rows = {r['path'] for r in db.execute('SELECT path FROM Images').fetchall()}
        self.assertNotIn(str(a.resolve()), rows)
        db.close()

    def test_all_blocked_returns_without_loading_model(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        blacklist.add(a)
        db = open_db(self.cache_dir / 'db' / 'dedup.db')

        with mock.patch('imhandler.embedder.load_clip_model', side_effect=AssertionError('should not load')):
            with mock.patch('imhandler.embedder.load_sscd_model', side_effect=AssertionError('should not load')):
                result = embed_images(self.root1, db, model='both')

        self.assertEqual(result, (0, 0, 1))
        db.close()

    def test_find_similar_omits_blocked_neighbor_and_blocked_target(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root1 / 'b.jpg')
        c = write_image(self.root1 / 'c.jpg')
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        emb = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        for p in (a, b, c):
            db.execute('INSERT INTO Images (path, mtime, clip_embedding) VALUES (?, ?, ?)',
                       (str(p.resolve()), 0.0, emb.tobytes()))
        db.commit()
        blacklist.add(b)

        target_row, neighbors = find_similar(db, a, 'clip')
        self.assertEqual({n['path'] for n in neighbors}, {str(c.resolve())})

        blacklist.add(a)
        target_row2, neighbors2 = find_similar(db, a, 'clip')
        self.assertIsNone(target_row2)
        self.assertEqual(neighbors2, [])
        db.close()

    def test_find_semantic_all_blocked_returns_empty_without_loading_model(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        emb = np.array([1.0, 0.0], dtype=np.float32)
        db.execute('INSERT INTO Images (path, mtime, clip_embedding) VALUES (?, ?, ?)',
                   (str(a.resolve()), 0.0, emb.tobytes()))
        db.commit()
        blacklist.add(a)

        with mock.patch('imhandler.embedder._load_clip_text_model',
                         side_effect=AssertionError('should not load')):
            results, count = find_semantic(db, 'query')

        self.assertEqual((results, count), ([], 0))
        db.close()


class DbAndClustererTests(ImhandlerFixtureTestCase):
    def _make_cluster(self, db, paths) -> tuple[int, list[int]]:
        ids = []
        for p in paths:
            db.execute('INSERT INTO Images (path, mtime) VALUES (?, ?)', (str(p), 0.0))
            ids.append(db.execute('SELECT last_insert_rowid()').fetchone()[0])
        db.execute('INSERT INTO Clusters (threshold_used, model_used) VALUES (?, ?)', (0.85, 'clip'))
        cluster_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        for rank, image_id in enumerate(ids):
            db.execute(
                'INSERT INTO ClusterMembership (cluster_id, image_id, quality_rank) VALUES (?, ?, ?)',
                (cluster_id, image_id, rank),
            )
        db.commit()
        return cluster_id, ids

    def test_get_cluster_members_omits_blocked_and_keeps_undersized(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root1 / 'b.jpg')
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        cluster_id, _ = self._make_cluster(db, [a.resolve(), b.resolve()])
        blacklist.add(a)

        members = get_cluster_members(db, cluster_id)

        self.assertEqual([m['path'] for m in members], [str(b.resolve())])
        db.close()

    def test_cluster_images_writes_no_cluster_when_one_of_pair_blocked(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root1 / 'b.jpg')
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        emb = np.array([1.0, 0.0], dtype=np.float32)
        for p in (a, b):
            db.execute('INSERT INTO Images (path, mtime, clip_embedding) VALUES (?, ?, ?)',
                       (str(p.resolve()), 0.0, emb.tobytes()))
        db.commit()
        blacklist.add(a)

        n = cluster_images(db, threshold=0.5, model='clip')

        self.assertEqual(n, 0)
        db.close()

    def test_cleanup_missing_members_is_blacklist_blind(self) -> None:
        """cleanup_missing_members must count a blocked-but-present member
        as present, not missing -- it is blacklist-blind by design (passes
        blocked=frozenset() to get_cluster_members), so a page view can
        never use it to shrink remaining_count based on hidden status and
        collapse a cluster that only has a hidden member, not a deleted
        one."""
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root1 / 'b.jpg')
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        cluster_id, _ = self._make_cluster(db, [a.resolve(), b.resolve()])
        blacklist.add(a)

        missing_ids, remaining = cleanup_missing_members(db, cluster_id)

        self.assertEqual(missing_ids, [])
        self.assertEqual(remaining, 2)
        rows = {r['path'] for r in db.execute('SELECT path FROM Images').fetchall()}
        self.assertEqual(rows, {str(a.resolve()), str(b.resolve())})
        memberships = db.execute(
            'SELECT image_id FROM ClusterMembership WHERE cluster_id = ?', (cluster_id,)
        ).fetchall()
        self.assertEqual(len(memberships), 2)
        db.close()


if __name__ == '__main__':
    unittest.main()

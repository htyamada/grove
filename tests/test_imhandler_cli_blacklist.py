import argparse
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from ._imhandler_fixture import ImhandlerFixtureTestCase, write_image

from imhandler import appconfig, blacklist, cache
from imhandler.cli import (
    _write_export,
    cmd_blacklist_export,
    cmd_cluster,
    cmd_embed,
    cmd_purge,
    cmd_report,
    cmd_scan,
    cmd_thumb,
)
from imhandler.db import open_db


def ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


class ListCommandTests(ImhandlerFixtureTestCase):
    def test_list_excludes_blocked(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        write_image(self.root1 / 'b.jpg')
        blacklist.add(a)
        buf = io.StringIO()

        with mock.patch('sys.stdout', buf):
            cmd_scan(ns(dir=str(self.root1), count=False, tree=False, glob=None, sort='name'), 'imh')

        self.assertNotIn('a.jpg', buf.getvalue())
        self.assertIn('b.jpg', buf.getvalue())

    def test_list_count_excludes_blocked(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        write_image(self.root1 / 'b.jpg')
        blacklist.add(a)
        buf = io.StringIO()

        with mock.patch('sys.stdout', buf):
            cmd_scan(ns(dir=str(self.root1), count=True, tree=False, glob=None, sort='name'), 'imh')

        self.assertEqual(buf.getvalue().strip(), '1')

    def test_list_unconfigured_cache_dir_exits_0_unfiltered(self) -> None:
        write_image(self.root1 / 'a.jpg')
        buf = io.StringIO()

        with mock.patch.object(appconfig, 'cache_dir', ''):
            with mock.patch('sys.stdout', buf):
                cmd_scan(ns(dir=str(self.root1), count=True, tree=False, glob=None, sort='name'), 'imh')

        self.assertEqual(buf.getvalue().strip(), '1')

    def test_corrupt_store_exits_1_and_does_no_work(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / 'blacklist.json').write_text('not json', encoding='utf-8')

        with self.assertRaises(SystemExit) as cm:
            cmd_scan(ns(dir=str(self.root1), count=False, tree=False, glob=None, sort='name'), 'imh')

        self.assertEqual(cm.exception.code, 1)


class ThumbCommandTests(ImhandlerFixtureTestCase):
    def test_thumb_skips_blocked_and_reports_hidden_count(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        write_image(self.root1 / 'b.jpg')
        blacklist.add(a)
        buf = io.StringIO()

        with mock.patch('sys.stdout', buf):
            cmd_thumb(ns(dir=str(self.root1), size=200, dry_run=False, verbose=False), 'imh')

        self.assertIn('1 hidden', buf.getvalue())
        self.assertEqual(len(list(cache.thumbs_dir().rglob('*.jpg'))), 1)

    def test_thumb_dry_run_reports_hidden_count(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        write_image(self.root1 / 'b.jpg')
        blacklist.add(a)
        buf = io.StringIO()

        with mock.patch('sys.stdout', buf):
            cmd_thumb(ns(dir=str(self.root1), size=200, dry_run=True, verbose=False), 'imh')

        self.assertIn('1 hidden', buf.getvalue())
        self.assertFalse(cache.thumbs_dir().exists())

    def test_corrupt_store_exits_1_generates_nothing(self) -> None:
        write_image(self.root1 / 'a.jpg')
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / 'blacklist.json').write_text('not json', encoding='utf-8')

        with self.assertRaises(SystemExit) as cm:
            cmd_thumb(ns(dir=str(self.root1), size=200, dry_run=False, verbose=False), 'imh')

        self.assertEqual(cm.exception.code, 1)
        self.assertFalse(cache.thumbs_dir().exists())


class EmbedCommandTests(ImhandlerFixtureTestCase):
    def test_embed_passes_snapshot_and_reports_excluded(self) -> None:
        write_image(self.root1 / 'a.jpg')
        buf = io.StringIO()
        args = ns(dir=str(self.root1), model='clip', db=str(self.cache_dir / 'db' / 'dedup.db'),
                  weights=None, batch_size=8, lap_lo=None, lap_hi=None, hf_lo=None,
                  block_hi=None, sc_hi=None)

        with mock.patch('imhandler.embedder.embed_images', return_value=(0, 0, 1)) as embed_mock:
            with mock.patch('sys.stdout', buf):
                cmd_embed(args, 'imh')

        self.assertIn('1 hidden', buf.getvalue())
        self.assertIn('blocked', embed_mock.call_args.kwargs)


class ClusterCommandTests(ImhandlerFixtureTestCase):
    def test_cluster_passes_snapshot(self) -> None:
        args = ns(db=str(self.cache_dir / 'db' / 'dedup.db'), model='clip', threshold=0.85)

        with mock.patch('imhandler.clusterer.cluster_images', return_value=0) as cluster_mock:
            cmd_cluster(args, 'imh')

        self.assertIn('blocked', cluster_mock.call_args.kwargs)


class ReportCommandTests(ImhandlerFixtureTestCase):
    def test_report_omits_blocked_members_and_undersized_clusters(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root1 / 'b.jpg')
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        ids = []
        for p in (a, b):
            db.execute('INSERT INTO Images (path, mtime) VALUES (?, ?)', (str(p.resolve()), 0.0))
            ids.append(db.execute('SELECT last_insert_rowid()').fetchone()[0])
        db.execute('INSERT INTO Clusters (threshold_used, model_used) VALUES (?, ?)', (0.85, 'clip'))
        cluster_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        for rank, image_id in enumerate(ids):
            db.execute(
                'INSERT INTO ClusterMembership (cluster_id, image_id, quality_rank) VALUES (?, ?, ?)',
                (cluster_id, image_id, rank),
            )
        db.commit()
        db.close()
        blacklist.add(a)
        buf = io.StringIO()

        with mock.patch('sys.stdout', buf):
            cmd_report(ns(model=None, threshold=None, db=str(self.cache_dir / 'db' / 'dedup.db'),
                          output=None), 'imh')

        output = buf.getvalue()
        self.assertNotIn(str(a.resolve()), output)
        self.assertNotIn(f'cluster {cluster_id} ', output)


class PurgeCommandTests(ImhandlerFixtureTestCase):
    def test_purge_dir_reports_skipped_sweep(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        db = open_db(self.cache_dir / 'db' / 'dedup.db')
        db.execute('INSERT INTO Images (path, mtime) VALUES (?, ?)', (str(a.resolve()), 0.0))
        db.commit()
        db.close()
        buf = io.StringIO()

        with mock.patch('sys.stdout', buf):
            cmd_purge(ns(dir=str(self.root1), dry_run=False), 'imh')

        self.assertIn('thumbnail sweep skipped', buf.getvalue())

    def test_corrupt_store_exits_1(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / 'blacklist.json').write_text('not json', encoding='utf-8')

        with self.assertRaises(SystemExit) as cm:
            cmd_purge(ns(dir=None, dry_run=False), 'imh')

        self.assertEqual(cm.exception.code, 1)


class ExportTests(ImhandlerFixtureTestCase):
    def _export_to_stdout(self, fmt: str) -> bytes:
        buf = io.BytesIO()
        with mock.patch('sys.stdout') as stdout_mock:
            stdout_mock.buffer = buf
            cmd_blacklist_export(ns(output=None, format=fmt), 'imh')
        return buf.getvalue()

    def test_all_formats_to_stdout_match_store_order(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        b = write_image(self.root1 / 'b.jpg')
        blacklist.add(b)
        blacklist.add(a)
        expected = sorted(str(p) for p in blacklist.load())

        self.assertEqual(self._export_to_stdout('paths').decode(),
                          ''.join(p + '\n' for p in expected))
        self.assertEqual(self._export_to_stdout('paths0').decode(),
                          ''.join(p + '\x00' for p in expected))
        self.assertEqual(json.loads(self._export_to_stdout('json').decode()),
                          {'version': 1, 'paths': expected})

    def test_json_export_byte_identical_to_store(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        blacklist.add(a)
        store_bytes = blacklist.store_path().read_bytes()

        self.assertEqual(self._export_to_stdout('json'), store_bytes)

    def test_empty_store_exports_empty(self) -> None:
        self.assertEqual(self._export_to_stdout('paths'), b'')

    def test_corrupt_store_exits_1(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / 'blacklist.json').write_text('not json', encoding='utf-8')

        with self.assertRaises(SystemExit) as cm:
            cmd_blacklist_export(ns(output=None, format='paths'), 'imh')

        self.assertEqual(cm.exception.code, 1)

    def test_unconfigured_cache_dir_exits_1_cleanly(self) -> None:
        with mock.patch.object(appconfig, 'cache_dir', ''):
            with self.assertRaises(SystemExit) as cm:
                cmd_blacklist_export(ns(output=None, format='paths'), 'imh')

        self.assertEqual(cm.exception.code, 1)

    def test_newline_entry_rejects_paths_format_with_no_output(self) -> None:
        bad = self.root1 / 'weird\ndir' / 'a.jpg'
        blacklist.add(bad)
        buf = io.BytesIO()

        with mock.patch('sys.stdout') as stdout_mock:
            stdout_mock.buffer = buf
            with self.assertRaises(SystemExit) as cm:
                cmd_blacklist_export(ns(output=None, format='paths'), 'imh')

        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(buf.getvalue(), b'')

    def test_carriage_return_entry_rejects_paths_format(self) -> None:
        bad = self.root1 / 'weird\rdir' / 'a.jpg'
        blacklist.add(bad)

        with self.assertRaises(SystemExit):
            cmd_blacklist_export(ns(output=None, format='paths'), 'imh')

    def test_newline_entry_round_trips_via_paths0_and_json(self) -> None:
        bad = self.root1 / 'weird\ndir' / 'a.jpg'
        blacklist.add(bad)

        p0 = self._export_to_stdout('paths0')
        self.assertIn(str(bad.resolve()).encode() + b'\x00', p0)

        doc = json.loads(self._export_to_stdout('json').decode())
        self.assertIn(str(bad.resolve()), doc['paths'])

    def test_o_writes_atomically(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        blacklist.add(a)
        dest = Path(self._tmp.name) / 'export.txt'

        cmd_blacklist_export(ns(output=str(dest), format='paths'), 'imh')

        self.assertEqual(dest.read_text(), str(a.resolve()) + '\n')

    def test_o_rejects_path_under_image_root(self) -> None:
        target = self.root1 / 'export.txt'

        with self.assertRaises(SystemExit) as cm:
            cmd_blacklist_export(ns(output=str(target), format='paths'), 'imh')

        self.assertEqual(cm.exception.code, 1)
        self.assertFalse(target.exists())

    def test_o_rejects_store_path(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        blacklist.add(a)
        mtime_before = blacklist.store_path().stat().st_mtime_ns

        with self.assertRaises(SystemExit) as cm:
            cmd_blacklist_export(ns(output=str(blacklist.store_path()), format='paths'), 'imh')

        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(blacklist.store_path().stat().st_mtime_ns, mtime_before)

    def test_o_dash_is_refused(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            cmd_blacklist_export(ns(output='-', format='paths'), 'imh')

        self.assertEqual(cm.exception.code, 1)
        self.assertFalse(Path('-').exists())

    def test_export_creates_no_lock_or_temp_files(self) -> None:
        a = write_image(self.root1 / 'a.jpg')
        blacklist.add(a)  # add() itself creates the lock file; export must not touch it further
        store_mtime_before = blacklist.store_path().stat().st_mtime_ns
        lock_path = self.cache_dir / '.blacklist.lock'
        lock_mtime_before = lock_path.stat().st_mtime_ns
        dest = Path(self._tmp.name) / 'export.txt'

        cmd_blacklist_export(ns(output=str(dest), format='paths'), 'imh')

        self.assertEqual(blacklist.store_path().stat().st_mtime_ns, store_mtime_before)
        self.assertEqual(lock_path.stat().st_mtime_ns, lock_mtime_before)
        leftovers = [p for p in self.cache_dir.iterdir() if p.name.startswith('.blacklist-')]
        self.assertEqual(leftovers, [])


class WriteExportUnitTests(unittest.TestCase):
    def test_paths_rejects_newline_and_cr_before_writing(self) -> None:
        buf = io.BytesIO()
        with self.assertRaises(ValueError):
            _write_export(['/a/b\nc.jpg', '/a/d.jpg'], 'paths', buf)
        self.assertEqual(buf.getvalue(), b'')

        buf2 = io.BytesIO()
        with self.assertRaises(ValueError):
            _write_export(['/a/b\rc.jpg'], 'paths', buf2)
        self.assertEqual(buf2.getvalue(), b'')

    def test_paths0_and_json_accept_newline_and_cr(self) -> None:
        paths = ['/a/b\nc.jpg', '/a/d\re.jpg']

        buf = io.BytesIO()
        _write_export(paths, 'paths0', buf)
        self.assertEqual(buf.getvalue(), b''.join(p.encode() + b'\x00' for p in paths))

        buf2 = io.BytesIO()
        _write_export(paths, 'json', buf2)
        self.assertEqual(json.loads(buf2.getvalue().decode())['paths'], paths)

    def test_undecodable_bytes_survive_via_fsencode(self) -> None:
        raw = os.fsdecode(b'/a/bad\xffname.jpg')
        buf = io.BytesIO()

        _write_export([raw], 'paths0', buf)

        self.assertEqual(buf.getvalue(), b'/a/bad\xffname.jpg\x00')

    def test_adversarial_names_written_as_literal_bytes_no_shebang(self) -> None:
        paths = [
            '/a/has space.jpg', "/a/o'clock.jpg", '/a/"q".jpg',
            '/a/back\\slash.jpg', '/a/$(rm -rf /).jpg', '/a/-dash.jpg', '/a/semi;colon.jpg',
        ]
        buf = io.BytesIO()

        _write_export(paths, 'paths', buf)

        data = buf.getvalue()
        for p in paths:
            self.assertIn(p.encode(), data)
        self.assertFalse(data.startswith(b'#!'))


if __name__ == '__main__':
    unittest.main()

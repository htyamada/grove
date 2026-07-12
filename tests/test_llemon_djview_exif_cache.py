"""Tests for the per-directory EXIF generation-metadata cache in storage.py."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'lib') not in sys.path:
    sys.path.insert(0, str(ROOT / 'lib'))

from llemon_djview.storage import (  # noqa: E402
    METADATA_CACHE_DIR,
    delete_image_asset,
    move_image_asset,
    read_image_sidecar,
)


META = {'model': 'model-x', 'prompt': 'draw a cat', 'files': ['img.png']}


def _identity(value):
    return value


class ExifMetadataCacheTests(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.media_dir = self._tmp.name

    def _write_image(self, fname: str = 'img.png') -> str:
        path = os.path.join(self.media_dir, fname)
        with open(path, 'wb') as f:
            f.write(b'fake-image')
        return path

    def _cache_path(self, fname: str = 'img.png') -> str:
        return os.path.join(self.media_dir, METADATA_CACHE_DIR, f'{fname}.json')

    def test_exif_read_is_cached(self) -> None:
        self._write_image()
        with mock.patch(
            'llemon_djview.storage.read_image_exif_metadata_result',
            return_value=(META, True),
        ) as reader:
            first = read_image_sidecar(self.media_dir, 'img.png', _identity)
            second = read_image_sidecar(self.media_dir, 'img.png', _identity)
        self.assertEqual(first, META)
        self.assertEqual(second, META)
        self.assertEqual(reader.call_count, 1)
        with open(self._cache_path(), encoding='utf-8') as f:
            self.assertEqual(json.load(f), META)

    def test_no_metadata_result_is_cached(self) -> None:
        self._write_image()
        with mock.patch(
            'llemon_djview.storage.read_image_exif_metadata_result',
            return_value=(None, True),
        ) as reader:
            first = read_image_sidecar(self.media_dir, 'img.png', _identity)
            second = read_image_sidecar(self.media_dir, 'img.png', _identity)
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(reader.call_count, 1)
        with open(self._cache_path(), encoding='utf-8') as f:
            self.assertIsNone(json.load(f))

    def test_transient_exif_failure_is_not_cached(self) -> None:
        self._write_image()
        with mock.patch(
            'llemon_djview.storage.read_image_exif_metadata_result',
            side_effect=[(None, False), (META, True)],
        ) as reader:
            first = read_image_sidecar(self.media_dir, 'img.png', _identity)
            second = read_image_sidecar(self.media_dir, 'img.png', _identity)
        self.assertIsNone(first)
        self.assertEqual(second, META)
        self.assertEqual(reader.call_count, 2)

    def test_cache_invalidated_when_image_is_newer(self) -> None:
        image_path = self._write_image()
        with mock.patch(
            'llemon_djview.storage.read_image_exif_metadata_result',
            return_value=(META, True),
        ) as reader:
            read_image_sidecar(self.media_dir, 'img.png', _identity)
            cache_mtime = os.path.getmtime(self._cache_path())
            os.utime(image_path, (cache_mtime + 10, cache_mtime + 10))
            read_image_sidecar(self.media_dir, 'img.png', _identity)
        self.assertEqual(reader.call_count, 2)

    def test_json_sidecar_bypasses_exif_read(self) -> None:
        self._write_image()
        sidecar = {'model': 'sidecar-model'}
        with open(os.path.join(self.media_dir, 'img.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(sidecar, f)
        with mock.patch(
            'llemon_djview.storage.read_image_exif_metadata_result',
        ) as reader:
            result = read_image_sidecar(self.media_dir, 'img.png', _identity)
        self.assertEqual(result, sidecar)
        reader.assert_not_called()

    def test_non_image_files_skip_exif_read(self) -> None:
        self._write_image('clip.mp4')
        with mock.patch(
            'llemon_djview.storage.read_image_exif_metadata_result',
        ) as reader:
            result = read_image_sidecar(self.media_dir, 'clip.mp4', _identity)
        self.assertIsNone(result)
        reader.assert_not_called()

    def test_delete_image_asset_removes_cache_entry(self) -> None:
        self._write_image()
        with mock.patch(
            'llemon_djview.storage.read_image_exif_metadata_result',
            return_value=(META, True),
        ):
            read_image_sidecar(self.media_dir, 'img.png', _identity)
        self.assertTrue(os.path.isfile(self._cache_path()))
        delete_image_asset(self.media_dir, 'img.png', '')
        self.assertFalse(os.path.isfile(self._cache_path()))

    def test_move_image_asset_moves_cache_entry(self) -> None:
        self._write_image()
        with mock.patch(
            'llemon_djview.storage.read_image_exif_metadata_result',
            return_value=(META, True),
        ):
            read_image_sidecar(self.media_dir, 'img.png', _identity)
        dst_dir = os.path.join(self.media_dir, 'archive')
        dst_fname = move_image_asset(self.media_dir, dst_dir, 'img.png', '', '')
        self.assertFalse(os.path.isfile(self._cache_path()))
        dst_cache = os.path.join(dst_dir, METADATA_CACHE_DIR, f'{dst_fname}.json')
        with open(dst_cache, encoding='utf-8') as f:
            self.assertEqual(json.load(f), META)


if __name__ == '__main__':
    unittest.main()

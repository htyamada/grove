import json
import os
import sys
import tempfile
import unittest
from unittest import mock

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from llemon_djview.media import _MediaImageViewSet
    from llemon_djview.storage import read_video_sidecar
else:
    try:
        from ..media import _MediaImageViewSet
        from ..storage import read_video_sidecar
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        from llemon_djview.media import _MediaImageViewSet
        from llemon_djview.storage import read_video_sidecar


class VideoMetadataTests(unittest.TestCase):
    def test_read_video_sidecar_falls_back_to_embedded_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, 'clip.mp4')
            with open(video_path, 'wb') as f:
                f.write(b'not-a-real-video')

            ffprobe_payload = {
                'format': {
                    'format_name': 'mov,mp4,m4a,3gp,3g2,mj2',
                    'duration': '2.500000',
                    'bit_rate': '123456',
                    'tags': {
                        'title': 'Test clip',
                        'creation_time': '2026-06-09T12:34:56Z',
                    },
                },
                'streams': [
                    {
                        'codec_type': 'video',
                        'codec_name': 'h264',
                        'width': 1920,
                        'height': 1080,
                    },
                    {
                        'codec_type': 'audio',
                        'codec_name': 'aac',
                    },
                ],
            }

            with mock.patch('llemon_djview.storage.shutil.which', return_value='/usr/bin/ffprobe'):
                with mock.patch('llemon_djview.storage.subprocess.run') as run_mock:
                    run_mock.return_value.stdout = json.dumps(ffprobe_payload)
                    meta = read_video_sidecar(tmpdir, 'clip.mp4', lambda value: value)

            self.assertEqual(meta['metadata_source'], 'embedded')
            self.assertEqual(meta['duration'], '2.500000')
            self.assertEqual(meta['resolution'], '1920x1080')
            self.assertEqual(meta['video_codec'], 'h264')
            self.assertEqual(meta['audio_codec'], 'aac')
            self.assertEqual(meta['title'], 'Test clip')
            self.assertEqual(meta['creation_time'], '2026-06-09T12:34:56Z')

    def test_combined_gallery_uses_video_metadata_reader_for_videos(self):
        viewset = _MediaImageViewSet('llemon_image', 'llemon_media')

        with mock.patch('llemon_djview.media.read_video_sidecar', return_value={'duration': '1.0'}) as read_mock:
            result = viewset._find_sidecar('/tmp/media', 'clip.mp4')

        self.assertEqual(result, {'duration': '1.0'})
        read_mock.assert_called_once()


if __name__ == '__main__':
    unittest.main()

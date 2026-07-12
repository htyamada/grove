"""Storage helpers owned by the LLemon Django media views."""

from __future__ import annotations

import base64
import errno
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any

from hty7.llemon.mediagen.imagegen import read_image_exif_metadata_result


IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
VIDEO_EXTS = {'.mp4', '.webm', '.mov', '.m4v'}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
LARGE_THUMB_SIZE = 600
METADATA_CACHE_DIR = 'metadata_cache'


def safe_name(value: str) -> str:
    name = os.path.basename(value or '').replace('\\', '')
    if not name or name.startswith('.'):
        raise ValueError('invalid filename')
    return name


def _replace_or_move(src: str, dst: str) -> None:
    """Replace on one filesystem; fall back to copy/unlink across filesystems."""
    try:
        os.replace(src, dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        shutil.move(src, dst)


def file_as_data_url(file_path: str, filename: str | None = None) -> str:
    name = filename or os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(name)
    if not mime_type:
        raise ValueError('unknown content type')
    with open(file_path, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('ascii')
    return f'data:{mime_type};base64,{encoded}'


def sanitize_metadata_data_urls(
    value: Any,
    *,
    max_length: int = 30,
    omitted_marker: str = '[data URL omitted]',
) -> Any:
    if isinstance(value, str):
        if value.lower().startswith('data:') and len(value) > max_length:
            return omitted_marker
        return value
    if isinstance(value, list):
        return [
            sanitize_metadata_data_urls(
                item, max_length=max_length, omitted_marker=omitted_marker,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            sanitize_metadata_data_urls(
                item, max_length=max_length, omitted_marker=omitted_marker,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: sanitize_metadata_data_urls(
                item, max_length=max_length, omitted_marker=omitted_marker,
            )
            for key, item in value.items()
        }
    return value


def unique_suffix_name(directory: str, filename: str) -> str:
    stem, ext = os.path.splitext(filename)
    candidate = filename
    n = 1
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f'{stem}_{n}{ext}'
        n += 1
    return candidate


def save_uploaded_image_files(files, target_dir: str) -> tuple[list[str], list[str]]:
    os.makedirs(target_dir, exist_ok=True)
    saved: list[str] = []
    errors: list[str] = []
    for upload in files:
        fname = os.path.basename(upload.name)
        if not fname or fname.startswith('.'):
            errors.append('invalid filename')
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in IMAGE_EXTS:
            errors.append(f'{fname}: unsupported format')
            continue
        fname = unique_suffix_name(target_dir, fname)
        dest = os.path.join(target_dir, fname)
        try:
            from PIL import Image
            upload.seek(0)
            with Image.open(upload) as opened:
                im: Image.Image = opened
                if max(im.width, im.height) > 2048:
                    im.thumbnail((2048, 2048))
                if ext in ('.jpg', '.jpeg') and im.mode not in ('RGB', 'L'):
                    im = im.convert('RGB')
                im.save(dest)
            saved.append(fname)
        except Exception as e:
            errors.append(f'{fname}: {e}')
    return saved, errors


def sidecar_path(anchor_file: str) -> str:
    return os.path.splitext(anchor_file)[0] + '.json'


def write_json_sidecar(
    anchor_file: str,
    payload: dict[str, Any],
    *,
    sort_keys: bool = False,
) -> str:
    path = sidecar_path(anchor_file)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, sort_keys=sort_keys)
    return path


def delete_media_file(directory: str, filename: str, allowed_exts: set[str]) -> None:
    fname = safe_name(filename)
    ext = os.path.splitext(fname)[1].lower()
    path = os.path.join(directory, fname)
    if ext not in allowed_exts or not os.path.isfile(path):
        raise FileNotFoundError(fname)
    os.remove(path)


def _image_exif_metadata_cached(media_dir: str, fname: str) -> dict[str, Any] | None:
    """Read EXIF-embedded generation metadata through a per-directory cache.

    Mirrors the thumbnail cache: ``<media_dir>/metadata_cache/<fname>.json``
    holds the read_image_exif_metadata() result (``null`` when the image has
    no embedded metadata) and is refreshed when the image is newer, so
    gallery listings do not spawn exiftool per sidecar-less file per load.
    """
    image_path = os.path.join(media_dir, fname)
    cache_dir = os.path.join(media_dir, METADATA_CACHE_DIR)
    cache_path = os.path.join(cache_dir, f'{fname}.json')
    try:
        if os.path.getmtime(cache_path) >= os.path.getmtime(image_path):
            with open(cache_path, encoding='utf-8') as f:
                cached = json.load(f)
            return cached if isinstance(cached, dict) else None
    except (OSError, ValueError):
        pass
    meta, cacheable = read_image_exif_metadata_result(image_path)
    if not cacheable:
        return meta
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f)
    except OSError:
        pass
    return meta


def read_image_sidecar(media_dir: str, fname: str, sanitize) -> dict[str, Any] | None:
    stem = os.path.splitext(fname)[0]
    bare = re.sub(r'_\d+$', '', stem)
    for candidate in dict.fromkeys([stem, bare]):
        path = os.path.join(media_dir, f'{candidate}.json')
        if os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as f:
                    return sanitize(json.load(f))
            except Exception:
                return None
    # No JSON sidecar — some backends (e.g. OpenRouter) embed metadata
    # directly in the image file's EXIF tags instead.
    if (os.path.splitext(fname)[1].lower() in IMAGE_EXTS
            and os.path.isfile(os.path.join(media_dir, fname))):
        meta = _image_exif_metadata_cached(media_dir, fname)
        if meta is not None:
            return sanitize(meta)
    return None


def read_video_sidecar(media_dir: str, fname: str, sanitize) -> dict[str, Any]:
    stem = os.path.splitext(fname)[0]
    for name in (f'{stem}.json', f'{fname}.json'):
        path = os.path.join(media_dir, name)
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            return sanitize(data) if isinstance(data, dict) else {}
        except FileNotFoundError:
            continue
        except Exception:
            continue
    for name in (f'{stem}.properties', f'{fname}.properties'):
        path = os.path.join(media_dir, name)
        try:
            props = {}
            with open(path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, value = line.split('=', 1)
                    props[key.strip()] = value.strip()
            return sanitize(props)
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return sanitize(read_embedded_video_metadata(media_dir, fname))


def _ffprobe_json(path: str) -> dict[str, Any]:
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return {}
    try:
        result = subprocess.run(
            [
                ffprobe,
                '-v', 'error',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                path,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
    except Exception:
        return {}
    try:
        data = json.loads(result.stdout)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_tag_value(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item for key, item in value.items()
        if item not in (None, '', [], {})
    }


def read_embedded_video_metadata(media_dir: str, fname: str) -> dict[str, Any]:
    path = os.path.join(media_dir, fname)
    if not os.path.isfile(path):
        return {}

    probed = _ffprobe_json(path)
    if not probed:
        return {}

    format_info = probed.get('format') if isinstance(probed.get('format'), dict) else {}
    stream_list = probed.get('streams') if isinstance(probed.get('streams'), list) else []
    video_stream = next(
        (
            stream for stream in stream_list
            if isinstance(stream, dict) and stream.get('codec_type') == 'video'
        ),
        {},
    )
    audio_stream = next(
        (
            stream for stream in stream_list
            if isinstance(stream, dict) and stream.get('codec_type') == 'audio'
        ),
        {},
    )

    tags: dict[str, str] = {}
    for source in (
        format_info.get('tags'),
        video_stream.get('tags') if isinstance(video_stream, dict) else None,
        audio_stream.get('tags') if isinstance(audio_stream, dict) else None,
    ):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            clean_key = str(key).strip()
            clean_value = _coerce_tag_value(value)
            if clean_key and clean_value and clean_key not in tags:
                tags[clean_key] = clean_value

    width = video_stream.get('width') if isinstance(video_stream, dict) else None
    height = video_stream.get('height') if isinstance(video_stream, dict) else None
    resolution = ''
    if width and height:
        resolution = f'{width}x{height}'

    creation_time = ''
    for key in ('creation_time', 'date', 'com.apple.quicktime.creationdate'):
        if tags.get(key):
            creation_time = tags[key]
            break

    return _compact_dict({
        'metadata_source': 'embedded',
        'format_name': _coerce_tag_value(format_info.get('format_name')),
        'duration': _coerce_tag_value(format_info.get('duration') or video_stream.get('duration')),
        'bit_rate': _coerce_tag_value(format_info.get('bit_rate')),
        'resolution': resolution,
        'video_codec': _coerce_tag_value(video_stream.get('codec_name')),
        'audio_codec': _coerce_tag_value(audio_stream.get('codec_name')),
        'creation_time': creation_time,
        'title': tags.get('title', ''),
        'comment': tags.get('comment', ''),
        'description': tags.get('description', ''),
        'tags': tags,
    })


def ensure_thumbnail(src_dir: str, dst_dir: str, fname: str, size: int) -> bool:
    src = os.path.join(src_dir, fname)
    dst = os.path.join(dst_dir, fname)
    if os.path.isfile(dst):
        return True
    try:
        from PIL import Image
        os.makedirs(dst_dir, exist_ok=True)
        with Image.open(src) as im:
            im.thumbnail((size, size))
            im.save(dst)
        return True
    except Exception:
        return False


def video_thumb_name(fname: str) -> str:
    return f'{os.path.splitext(fname)[0]}.jpg'


def ensure_video_thumbnail(
    media_dir: str, fname: str, thumb_dir: str, *, size: int, quality: str,
) -> bool:
    fname = safe_name(fname)
    ext = os.path.splitext(fname)[1].lower()
    src = os.path.join(media_dir, fname)
    if ext not in VIDEO_EXTS or not os.path.isfile(src):
        return False
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return False
    dst = os.path.join(thumb_dir, video_thumb_name(fname))
    if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
        return True
    os.makedirs(thumb_dir, exist_ok=True)
    tmp = f'{dst}.{uuid.uuid4().hex}.tmp.jpg'
    for timestamp in ('00:00:01', '00:00:00.1', '00:00:00'):
        try:
            subprocess.run(
                [
                    ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                    '-ss', timestamp, '-i', src,
                    '-frames:v', '1',
                    '-vf', f'scale={size}:{size}:force_original_aspect_ratio=decrease',
                    '-q:v', quality,
                    tmp,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, dst)
                return True
        except Exception:
            pass
        finally:
            if os.path.isfile(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    return False


def companion_paths(directory: str, fname: str) -> list[str]:
    stem = os.path.splitext(fname)[0]
    names = (
        f'{stem}.json',
        f'{stem}.properties',
        f'{stem}.txt',
        f'{fname}.json',
        f'{fname}.properties',
    )
    return [os.path.join(directory, name) for name in dict.fromkeys(names)]


def delete_image_asset(
    media_dir: str,
    filename: str,
    thumb_dir: str,
    large_thumb_dir: str = '',
) -> None:
    fname = safe_name(filename)
    image_path = os.path.join(media_dir, fname)
    if not os.path.isfile(image_path):
        raise FileNotFoundError(fname)

    stem = os.path.splitext(fname)[0]
    bare = re.sub(r'_\d+$', '', stem)
    found_sidecar_path = None
    sidecar_bare = None
    for candidate in dict.fromkeys([stem, bare]):
        path = os.path.join(media_dir, f'{candidate}.json')
        if os.path.isfile(path):
            found_sidecar_path = path
            sidecar_bare = candidate
            break

    os.unlink(image_path)
    if found_sidecar_path and sidecar_bare is not None:
        siblings = [
            f for f in os.listdir(media_dir)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS
            and re.sub(r'_\d+$', '', os.path.splitext(f)[0]) == sidecar_bare
        ]
        if not siblings:
            try:
                os.unlink(found_sidecar_path)
            except OSError:
                pass

    for directory in filter(None, [thumb_dir, large_thumb_dir]):
        path = os.path.join(directory, fname)
        if os.path.isfile(path):
            try:
                os.unlink(path)
            except OSError:
                pass

    cache_path = os.path.join(media_dir, METADATA_CACHE_DIR, f'{fname}.json')
    if os.path.isfile(cache_path):
        try:
            os.unlink(cache_path)
        except OSError:
            pass


def delete_video_asset(
    directory: str,
    filename: str,
    thumb_dir: str = '',
    large_thumb_dir: str = '',
) -> None:
    fname = safe_name(filename)
    ext = os.path.splitext(fname)[1].lower()
    path = os.path.join(directory, fname)
    if ext not in VIDEO_EXTS or not os.path.isfile(path):
        raise FileNotFoundError(fname)
    os.remove(path)
    for companion in companion_paths(directory, fname):
        if os.path.isfile(companion):
            os.remove(companion)
    for directory_ in filter(None, [thumb_dir, large_thumb_dir]):
        thumb_path = os.path.join(directory_, video_thumb_name(fname))
        if os.path.isfile(thumb_path):
            os.remove(thumb_path)


def move_image_asset(
    src_dir: str,
    dst_dir: str,
    filename: str,
    src_thumb_dir: str,
    dst_thumb_dir: str,
    src_large_thumb_dir: str = '',
    dst_large_thumb_dir: str = '',
) -> str:
    fname = safe_name(filename)
    src_path = os.path.join(src_dir, fname)
    if not os.path.isfile(src_path):
        raise FileNotFoundError(fname)

    os.makedirs(dst_dir, exist_ok=True)
    dst_fname = unique_suffix_name(dst_dir, fname)
    stem, _ext = os.path.splitext(fname)
    bare = re.sub(r'_\d+$', '', stem)
    found_sidecar_path = None
    sidecar_bare = None
    for candidate in dict.fromkeys([stem, bare]):
        path = os.path.join(src_dir, f'{candidate}.json')
        if os.path.isfile(path):
            found_sidecar_path = path
            sidecar_bare = candidate
            break

    _replace_or_move(src_path, os.path.join(dst_dir, dst_fname))

    if found_sidecar_path and sidecar_bare is not None:
        siblings = [
            f for f in os.listdir(src_dir)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS
            and re.sub(r'_\d+$', '', os.path.splitext(f)[0]) == sidecar_bare
        ]
        if not siblings:
            dst_sidecar = os.path.join(dst_dir, os.path.basename(found_sidecar_path))
            try:
                if os.path.exists(dst_sidecar):
                    os.unlink(dst_sidecar)
                _replace_or_move(found_sidecar_path, dst_sidecar)
            except OSError:
                pass

    for src_thumb, dst_thumb in (
        (src_thumb_dir, dst_thumb_dir),
        (src_large_thumb_dir, dst_large_thumb_dir),
    ):
        if not src_thumb or not dst_thumb:
            continue
        src_thumb_path = os.path.join(src_thumb, fname)
        if os.path.isfile(src_thumb_path):
            try:
                os.makedirs(dst_thumb, exist_ok=True)
                _replace_or_move(src_thumb_path, os.path.join(dst_thumb, dst_fname))
            except OSError:
                pass

    src_cache = os.path.join(src_dir, METADATA_CACHE_DIR, f'{fname}.json')
    if os.path.isfile(src_cache):
        dst_cache_dir = os.path.join(dst_dir, METADATA_CACHE_DIR)
        try:
            os.makedirs(dst_cache_dir, exist_ok=True)
            _replace_or_move(src_cache, os.path.join(dst_cache_dir, f'{dst_fname}.json'))
        except OSError:
            pass
    return dst_fname


def move_video_asset(
    src_dir: str,
    dst_dir: str,
    filename: str,
    src_thumb_dir: str = '',
    dst_thumb_dir: str = '',
    src_large_thumb_dir: str = '',
    dst_large_thumb_dir: str = '',
) -> str:
    fname = safe_name(filename)
    ext = os.path.splitext(fname)[1].lower()
    if ext not in VIDEO_EXTS:
        raise ValueError('invalid video filename')
    src = os.path.join(src_dir, fname)
    if not os.path.isfile(src):
        raise FileNotFoundError(fname)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, fname)
    if os.path.exists(dst):
        raise FileExistsError(fname)
    shutil.move(src, dst)
    for companion in companion_paths(src_dir, fname):
        if not os.path.isfile(companion):
            continue
        target = os.path.join(dst_dir, os.path.basename(companion))
        if os.path.exists(target):
            continue
        shutil.move(companion, target)
    for src_t, dst_t in (
        (src_thumb_dir, dst_thumb_dir),
        (src_large_thumb_dir, dst_large_thumb_dir),
    ):
        if not src_t or not dst_t:
            continue
        src_thumb_path = os.path.join(src_t, video_thumb_name(fname))
        if os.path.isfile(src_thumb_path):
            os.makedirs(dst_t, exist_ok=True)
            shutil.move(src_thumb_path, os.path.join(dst_t, video_thumb_name(fname)))
    return fname


def image_as_data_url(directory: str, filename: str) -> str:
    fname = safe_name(filename)
    file_path = os.path.join(directory, fname)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(fname)
    return file_as_data_url(file_path, fname)


def save_operation_images(
    write_images,
    images: list[dict[str, Any]],
    media_dir: str,
    stem: str,
) -> tuple[list[str], str]:
    os.makedirs(media_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    desc = os.path.join(media_dir, f'{stem}_{ts}.txt')
    saved = write_images(images, desc)
    return [os.path.basename(s.path) for s in saved], desc


def write_operation_sidecar(anchor_file: str, payload: dict[str, Any]) -> str:
    return write_json_sidecar(anchor_file, payload)


_VALID_VIDEO_FMTS = {'mp4', 'webm', 'mov', 'm4v'}


def save_generated_videos(videos: list[dict[str, Any]], output_dir: str) -> list[str]:
    os.makedirs(output_dir, exist_ok=True)
    saved = []
    base = datetime.now(timezone.utc).strftime('video_%Y%m%d_%H%M%S')
    uid = uuid.uuid4().hex[:8]
    for i, video in enumerate(videos):
        fmt = (video.get('format') or 'mp4').strip('.').lower()
        if fmt not in _VALID_VIDEO_FMTS:
            fmt = 'mp4'
        suffix = '' if i == 0 else f'_{i}'
        fname = f'{base}_{uid}{suffix}.{fmt}'
        path = os.path.join(output_dir, fname)
        with open(path, 'wb') as f:
            f.write(video['data'])
        saved.append(fname)
    return saved


def write_video_sidecar(directory: str, filename: str, payload: dict[str, Any]) -> str:
    return write_json_sidecar(os.path.join(directory, filename), payload, sort_keys=True)


_CATEGORY_DDL = [
    """
    CREATE TABLE IF NOT EXISTS Category (
        id   INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS CategoryFile (
        category_id INTEGER NOT NULL REFERENCES Category(id) ON DELETE CASCADE,
        filename    TEXT NOT NULL,
        PRIMARY KEY (category_id, filename)
    )
    """,
]


class CategoryStore:
    """Category storage for the Django gallery."""

    def __init__(self, gallery_dir: str):
        self._gallery_dir = gallery_dir

    def _connect(self) -> sqlite3.Connection:
        if not self._gallery_dir:
            raise EnvironmentError('gallery directory is not configured')
        db_dir = os.path.join(self._gallery_dir, 'db')
        os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(os.path.join(db_dir, 'gallery.db'))
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        for ddl in _CATEGORY_DDL:
            conn.execute(ddl)
        conn.commit()
        return conn

    def rows(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT c.id, c.name, COUNT(f.filename) AS count
                FROM Category c
                LEFT JOIN CategoryFile f ON f.category_id = c.id
                GROUP BY c.id
                ORDER BY c.name COLLATE NOCASE
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def ids_by_file(self) -> dict[str, set[int]]:
        conn = self._connect()
        try:
            rows = conn.execute('SELECT category_id, filename FROM CategoryFile').fetchall()
            by_file: dict[str, set[int]] = {}
            for row in rows:
                by_file.setdefault(row['filename'], set()).add(row['category_id'])
            return by_file
        finally:
            conn.close()

    def file_set(self, category_id: int) -> set[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                'SELECT filename FROM CategoryFile WHERE category_id = ?',
                (category_id,),
            ).fetchall()
            return {r['filename'] for r in rows}
        finally:
            conn.close()

    def create(self, name: str) -> None:
        conn = self._connect()
        try:
            conn.execute('INSERT OR IGNORE INTO Category (name) VALUES (?)', (name,))
            conn.commit()
        finally:
            conn.close()

    def delete(self, category_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute('DELETE FROM Category WHERE id = ?', (category_id,))
            conn.commit()
        finally:
            conn.close()

    def set_file_state(self, category_id: int, filename: str, state: str = '') -> None:
        conn = self._connect()
        try:
            existing = conn.execute(
                'SELECT 1 FROM CategoryFile WHERE category_id = ? AND filename = ?',
                (category_id, filename),
            ).fetchone()
            if state == '1' and not existing:
                conn.execute(
                    'INSERT INTO CategoryFile (category_id, filename) VALUES (?, ?)',
                    (category_id, filename),
                )
            elif state == '0' and existing:
                conn.execute(
                    'DELETE FROM CategoryFile WHERE category_id = ? AND filename = ?',
                    (category_id, filename),
                )
            elif not state:
                if existing:
                    conn.execute(
                        'DELETE FROM CategoryFile WHERE category_id = ? AND filename = ?',
                        (category_id, filename),
                    )
                else:
                    conn.execute(
                        'INSERT INTO CategoryFile (category_id, filename) VALUES (?, ?)',
                        (category_id, filename),
                    )
            conn.commit()
        finally:
            conn.close()

    def remove_file(self, filename: str) -> None:
        conn = self._connect()
        try:
            conn.execute('DELETE FROM CategoryFile WHERE filename = ?', (filename,))
            conn.commit()
        finally:
            conn.close()

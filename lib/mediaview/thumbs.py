import hashlib
import subprocess
from pathlib import Path

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

IMAGE_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif', '.tiff', '.bmp', '.avif'})
VIDEO_EXTS = frozenset({'.mp4', '.mov', '.avi', '.webm', '.mkv', '.m4v', '.mpg', '.mpeg', '.wmv', '.flv'})
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTS


def _cache_digest(src: Path) -> str:
    return hashlib.sha256(str(src).encode()).hexdigest()


def _cache_path(src: Path, long_edge: int) -> Path:
    from .conf import cache_dir
    digest = _cache_digest(src)
    return cache_dir() / 'thumbs' / digest[:2] / f'{digest}-{long_edge}.jpg'


def invalidate(src: Path) -> None:
    from .conf import cache_dir

    digest = _cache_digest(src)
    thumb_dir = cache_dir() / 'thumbs' / digest[:2]
    for cached in thumb_dir.glob(f'{digest}-*.jpg'):
        cached.unlink(missing_ok=True)


def get_or_create(src: Path, long_edge: int = 200) -> 'Path | None':
    dest = _cache_path(src, long_edge)
    try:
        src_mtime = src.stat().st_mtime
    except OSError:
        return None
    if dest.exists() and dest.stat().st_mtime >= src_mtime:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if is_image(src):
        return _make_image_thumb(src, dest, long_edge)
    if is_video(src):
        return _make_video_thumb(src, dest, long_edge)
    return None


def _make_image_thumb(src: Path, dest: Path, long_edge: int) -> 'Path | None':
    try:
        from PIL import Image
        img = Image.open(src)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.thumbnail((long_edge, long_edge))
        img.save(dest, 'JPEG', quality=85)
        return dest
    except Exception:
        return None


def _make_video_thumb(src: Path, dest: Path, long_edge: int) -> 'Path | None':
    tmp = dest.with_suffix('.tmp.jpg')
    try:
        result = subprocess.run(
            [
                'ffmpeg', '-y', '-ss', '00:00:01', '-i', str(src),
                '-vframes', '1',
                '-vf', f'scale={long_edge}:{long_edge}:force_original_aspect_ratio=decrease',
                str(tmp),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and tmp.exists():
            tmp.rename(dest)
            return dest
    except Exception:
        pass
    if tmp.exists():
        tmp.unlink(missing_ok=True)
    return None

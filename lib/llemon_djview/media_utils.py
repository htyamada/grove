"""Shared utilities for unified media gallery (images and videos)."""

import os
from typing import Any

from .storage import IMAGE_EXTS, VIDEO_EXTS, ensure_thumbnail, ensure_video_thumbnail


def ensure_media_thumbnail(media_dir: str, thumb_dir: str, fname: str, size: int, *, quality: str = '3') -> bool:
    """Create thumbnail for image or video file, choosing appropriate method."""
    ext = os.path.splitext(fname)[1].lower()
    if ext in VIDEO_EXTS:
        return ensure_video_thumbnail(media_dir, fname, thumb_dir, size=size, quality=quality)
    return ensure_thumbnail(media_dir, thumb_dir, fname, size)


def list_media_files(media_dir: str) -> list[str]:
    """List all image and video files in a directory."""
    if not media_dir or not os.path.isdir(media_dir):
        return []
    result = []
    for fname in sorted(os.listdir(media_dir), reverse=True):
        ext = os.path.splitext(fname)[1].lower()
        if ext in (IMAGE_EXTS | VIDEO_EXTS):
            result.append(fname)
    return result


def is_image(fname: str) -> bool:
    """Check if file is an image."""
    ext = os.path.splitext(fname)[1].lower()
    return ext in IMAGE_EXTS


def is_video(fname: str) -> bool:
    """Check if file is a video."""
    ext = os.path.splitext(fname)[1].lower()
    return ext in VIDEO_EXTS

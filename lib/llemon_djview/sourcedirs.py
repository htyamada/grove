"""llemon_djview.sourcedirs -- source directory browser utilities.

Source directories are configured in Grove's etc/llemon_djview.conf overlay
under [*.llemon.mediagen]:

    input_files = [
        "Photos=~/Pictures",
        "Stock=/data/stock-images",
    ]
    source_thumb_dir = "~/var/hty7/llemon/mediagen/source_thumbs"  # optional

Each input_files entry is "nickname=path". Thumbnails are cached in
source_thumb_dir (or {media_dir}/source_thumbs/). Originals are never modified.
"""

import os

from .storage import IMAGE_EXTS, ensure_thumbnail


def get_source_dirs() -> list[dict]:
    """Return configured source directories from the mediagen config."""
    from hty7.llemon.mediagen.imagegen import get_source_dirs as _get
    return _get()


def source_thumb_base() -> str:
    """Return the base directory for the source thumbnail cache.

    Uses source_thumb_dir from mediagen config if set, otherwise falls back
    to {media_dir}/source_thumbs/.
    """
    from hty7.llemon.mediagen.imagegen import get_source_thumb_dir, get_media_dir
    thumb_dir = get_source_thumb_dir()
    if thumb_dir:
        return thumb_dir
    media_dir = get_media_dir()
    return os.path.join(media_dir, 'source_thumbs') if media_dir else ''


def validate_nickname(nickname: str, source_dirs: list[dict]) -> dict:
    """Return the source dir entry for nickname, or raise ValueError."""
    for sd in source_dirs:
        if sd.get('name') == nickname:
            return sd
    raise ValueError(f'unknown source directory: {nickname!r}')


def validate_subdir(raw: str) -> str:
    """Validate and normalise a relative subdirectory path.

    Returns '' for the root of the source dir. Raises ValueError on any
    path traversal attempt (e.g. '..').
    """
    if not raw:
        return ''
    parts = raw.replace('\\', '/').split('/')
    clean = []
    for part in parts:
        if not part or part == '.':
            continue
        if part == '..':
            raise ValueError('path traversal not allowed')
        clean.append(part)
    return '/'.join(clean)


def safe_source_filename(filename: str) -> str:
    """Validate a source dir image filename. Raises ValueError if invalid."""
    if not filename or '/' in filename or '\\' in filename or filename.startswith('.'):
        raise ValueError('invalid filename')
    ext = os.path.splitext(filename)[1].lower()
    if ext not in IMAGE_EXTS:
        raise ValueError('unsupported image format')
    return filename


def get_real_path(root: str, subdir: str, filename: str = '') -> str:
    """Build and validate an absolute path within root.

    Raises ValueError if the resolved path escapes root (e.g. via symlinks).
    """
    components: list[str] = [root]
    if subdir:
        components.extend(subdir.split('/'))
    if filename:
        components.append(filename)
    result = os.path.normpath(os.path.join(*components))
    root_real = os.path.realpath(root)
    result_real = os.path.realpath(result)
    if not (result_real == root_real or result_real.startswith(root_real + os.sep)):
        raise ValueError('path outside source directory')
    return result


def source_thumb_dir(thumb_base: str, nickname: str, subdir: str) -> str:
    """Return the thumbnail cache directory for nickname + subdir."""
    parts: list[str] = [thumb_base, nickname]
    if subdir:
        parts.extend(subdir.split('/'))
    parts.append('thumbnails')
    return os.path.join(*parts)


def source_large_thumb_dir(thumb_base: str, nickname: str, subdir: str) -> str:
    """Return the large thumbnail cache directory for nickname + subdir."""
    parts: list[str] = [thumb_base, nickname]
    if subdir:
        parts.extend(subdir.split('/'))
    parts.append('thumbnails_large')
    return os.path.join(*parts)


def ensure_source_thumbnail(
    current_dir: str,
    thumb_base: str,
    nickname: str,
    subdir: str,
    fname: str,
    size: int = 160,
) -> bool:
    """Ensure a 160-px thumbnail exists for a source dir image. Returns True if available."""
    t_dir = source_thumb_dir(thumb_base, nickname, subdir)
    return ensure_thumbnail(current_dir, t_dir, fname, size)


def ensure_source_large_thumbnail(
    current_dir: str,
    thumb_base: str,
    nickname: str,
    subdir: str,
    fname: str,
    size: int = 600,
) -> bool:
    """Ensure a 600-px large thumbnail exists for a source dir image. Returns True if available."""
    t_dir = source_large_thumb_dir(thumb_base, nickname, subdir)
    return ensure_thumbnail(current_dir, t_dir, fname, size)


def resolve_source_dir_path(path: str, ns: str) -> 'tuple[str, str] | None':
    """Resolve a URL path as a source_dirs_file URL for the given namespace.

    Returns (absolute_file_path, filename) or None if the path is not a source
    dir file URL or fails validation. Used by video generation to convert source
    dir image URLs to data: URLs before sending to provider APIs.
    """
    from django.urls import resolve, Resolver404  # type: ignore[import-untyped]
    try:
        match = resolve(path)
    except Resolver404:
        return None
    if match.url_name != 'source_dirs_file' or match.namespace != ns:
        return None

    nick: str = match.kwargs.get('nick', '')
    rp: str = match.kwargs.get('rp', '')  # relative path, e.g. 'vacation/beach.jpg'

    source_dirs = get_source_dirs()
    try:
        sd = validate_nickname(nick, source_dirs)
    except ValueError:
        return None

    if '/' in rp:
        subdir_part, fname = rp.rsplit('/', 1)
    else:
        subdir_part, fname = '', rp

    try:
        subdir_part = validate_subdir(subdir_part)
        fname = safe_source_filename(fname)
        file_path = get_real_path(sd['path'], subdir_part, fname)
    except ValueError:
        return None

    if not os.path.isfile(file_path):
        return None
    return file_path, fname

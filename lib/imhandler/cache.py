from pathlib import Path

from imhandler import appconfig


def image_root_entries() -> list[tuple[Path, str]]:
    """Return (resolved_path, display_name) pairs for all configured roots."""
    paths = appconfig.image_roots
    names = appconfig.image_root_names
    if not paths:
        raise EnvironmentError('image_root is not configured in etc/imhandler.conf')
    result: list[tuple[Path, str]] = []
    for path_str, name in zip(paths, names):
        p = Path(path_str).expanduser().resolve()
        if not p.is_dir():
            raise EnvironmentError(f'image_root does not exist or is not a directory: {p}')
        result.append((p, name))
    return result


def image_roots() -> list[Path]:
    return [p for p, _ in image_root_entries()]


def image_root() -> Path:
    return image_root_entries()[0][0]


def configured_image_roots() -> list[Path]:
    """Configured image_root paths, resolved but never required to exist.

    Unlike image_root_entries()/image_roots(), this does not stat() or
    require each root to currently be a directory -- callers that only
    need root *identity* for path validation (not to actually read files
    under it) use this so an offline/unmounted archive volume doesn't
    block validation for a path under a different, available root, or
    block validating a stale path under the offline root itself. Still
    raises EnvironmentError if image_root is entirely unconfigured, same
    as image_root_entries().
    """
    paths = appconfig.image_roots
    if not paths:
        raise EnvironmentError('image_root is not configured in etc/imhandler.conf')
    return [Path(p).expanduser().resolve() for p in paths]


def cache_root() -> Path:
    val = appconfig.cache_dir
    if not val:
        raise EnvironmentError('cache_dir is not configured in etc/imhandler.conf')
    return Path(val)


def thumbs_dir() -> Path:
    return cache_root() / 'thumbs'


def db_path() -> Path:
    return cache_root() / 'db' / 'dedup.db'


def weights_dir() -> Path:
    return cache_root() / 'weights'

"""imhandler.appconfig — application-level path configuration.

Call init(ac) once at frontend startup with an AppConfig instance.
Other modules read image_roots, image_root_names, and cache_dir from this module.

image_roots is a list of path strings; image_root_names is the parallel list of
display names (defaults to each path's basename when not explicitly configured).
"""
from __future__ import annotations

import os
from pathlib import Path

from hty7.config import AppConfig

image_roots: list[str] = []
image_root_names: list[str] = []
cache_dir: str = ''
_DEFAULT_CONF = str(Path(__file__).resolve().parents[2] / 'etc' / 'imhandler.conf')


def init(ac: AppConfig) -> None:
    """Set module globals from AppConfig (variant already selected)."""
    global image_roots, image_root_names, cache_dir
    raw = ac.get('imhandler', 'core', 'image_root') or []
    if isinstance(raw, str):
        items: list[str | dict[str, str]] = [raw] if raw else []
    elif isinstance(raw, list):
        items = list(raw)
    else:
        items = []

    roots: list[str] = []
    names: list[str] = []
    for item in items:
        if isinstance(item, str):
            roots.append(item)
            names.append(Path(item).expanduser().name)
        elif isinstance(item, dict):
            p = str(item.get('path', ''))
            n = str(item.get('name', '') or Path(p).expanduser().name)
            roots.append(p)
            names.append(n)
    image_roots = roots
    image_root_names = names
    cache_dir = str(ac.get('imhandler', 'core', 'cache_dir') or '')


def init_variant(variant: str, conf_path: str = _DEFAULT_CONF) -> None:
    """Load the source-tree imhandler config for variant and initialize globals."""
    init(AppConfig(os.path.expanduser(conf_path), variant))

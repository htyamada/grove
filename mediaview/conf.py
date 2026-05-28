import tomllib
from pathlib import Path

from django.conf import settings

_CONF_PATH = Path('~/etc/mediaview.conf').expanduser()
_LABEL = getattr(settings, 'MEDIAVIEW_LABEL', 'hty7')
_cached = None


def _load():
    global _cached
    if _cached is None:
        with open(_CONF_PATH, 'rb') as f:
            _cached = tomllib.load(f)
    return _cached


def config():
    return _load()


def roots():
    section = config().get(_LABEL, {})
    return {r['name']: Path(r['path']).expanduser().resolve() for r in section.get('roots', [])}


def cache_dir() -> Path:
    return Path(config()['cache_dir']).expanduser().resolve()

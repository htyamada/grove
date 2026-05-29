import fnmatch
from enum import Enum
from typing import Sequence

from .models import ImageEntry


class SortKey(Enum):
    NAME = 'name'
    MTIME = 'mtime'
    SIZE = 'size'


def filter_images(
    images: Sequence[ImageEntry],
    glob: str | None = None,
    mtime_after: float | None = None,
    mtime_before: float | None = None,
) -> list[ImageEntry]:
    result = list(images)
    if glob is not None:
        result = [e for e in result if fnmatch.fnmatch(e.path.name, glob)]
    if mtime_after is not None:
        result = [e for e in result if e.mtime > mtime_after]
    if mtime_before is not None:
        result = [e for e in result if e.mtime < mtime_before]
    return result


def sort_images(images: Sequence[ImageEntry], key: SortKey = SortKey.NAME) -> list[ImageEntry]:
    match key:
        case SortKey.NAME:
            return sorted(images, key=lambda e: e.path.name)
        case SortKey.MTIME:
            return sorted(images, key=lambda e: e.mtime)
        case SortKey.SIZE:
            return sorted(images, key=lambda e: e.path.stat().st_size)
    return list(images)


def filter_and_sort(
    images: Sequence[ImageEntry],
    glob: str | None = None,
    mtime_after: float | None = None,
    mtime_before: float | None = None,
    sort: SortKey = SortKey.NAME,
) -> list[ImageEntry]:
    return sort_images(filter_images(images, glob, mtime_after, mtime_before), sort)

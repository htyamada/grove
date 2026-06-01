"""llemon_djview.base_viewset - Shared base class for media view sets."""

import os
from typing import Any

from django.conf import settings  # type: ignore[import-untyped]
from django.urls import reverse  # type: ignore[import-untyped]

from .media_utils import ensure_media_thumbnail
from .storage import CategoryStore


class MediaGenViewSetBase:
    """Base class for media generation view sets (image and video)."""

    def __init__(self, template_prefix: str, url_namespace: str, *, base_nav=None,
                 nav=None, nav_suffix=None):
        self._tp         = template_prefix
        self._ns         = url_namespace
        self._base_nav   = base_nav
        self._nav_prefix = list(nav) if nav else []
        self._nav_suffix = list(nav_suffix) if nav_suffix else []

    def _t(self, name: str) -> str:
        """Get template path with prefix."""
        return f'{self._tp}/{name}'

    def _u(self, name: str, *args) -> str:
        """Get URL with namespace."""
        if args:
            return reverse(f'{self._ns}:{name}', args=args)
        return reverse(f'{self._ns}:{name}')

    def _ctx(self, title: str, nav: list, extra: dict) -> dict:
        """Build template context with standard fields."""
        ctx = {'title': title, 'nav': self._nav_prefix + nav + self._nav_suffix}
        if self._base_nav is not None:
            ctx['base_nav'] = self._base_nav
        ctx.update(extra)
        return ctx

    def _media_dir(self) -> str:
        """Get the media directory. Override in subclass."""
        raise NotImplementedError

    def _gallery_dir(self) -> str:
        """Get the gallery directory, with fallback to media_dir/gallery."""
        gallery_dir = getattr(settings, 'LLEMON_GALLERY_DIR', '')
        if gallery_dir:
            return gallery_dir
        media_dir = self._media_dir()
        return os.path.join(media_dir, 'gallery') if media_dir else ''

    def _archive_dir(self) -> str:
        """Get the archive directory, with fallback to media_dir/archive."""
        archive_dir = getattr(settings, 'LLEMON_ARCHIVE_DIR', '')
        if archive_dir:
            return archive_dir
        media_dir = self._media_dir()
        return os.path.join(media_dir, 'archive') if media_dir else ''

    def _gallery_category_db(self) -> CategoryStore:
        """Open the gallery category database."""
        return CategoryStore(self._gallery_dir())

    @staticmethod
    def _category_rows(conn: CategoryStore) -> list:
        """Get all category rows from database."""
        return conn.rows()

    @staticmethod
    def _category_file_set(conn: CategoryStore, category_id: int) -> set[str]:
        """Get the set of files in a category."""
        return conn.file_set(category_id)

    @staticmethod
    def _category_ids_by_file(conn: CategoryStore) -> dict[str, set[int]]:
        """Get mapping of files to category IDs."""
        return conn.ids_by_file()

    def _thumb_dir(self, media_dir: str = '') -> str:
        """Get thumbnail directory for a media directory."""
        if not media_dir:
            media_dir = self._gallery_dir()
        return os.path.join(media_dir, 'thumbnails') if media_dir else ''

    def _large_thumb_dir(self, media_dir: str = '') -> str:
        """Get large thumbnail directory for a media directory."""
        if not media_dir:
            media_dir = self._gallery_dir()
        return os.path.join(media_dir, 'thumbnails_large') if media_dir else ''

    def _archive_thumb_dir(self) -> str:
        """Get archive thumbnail directory."""
        archive_dir = self._archive_dir()
        return os.path.join(archive_dir, 'thumbnails') if archive_dir else ''

    def _archive_large_thumb_dir(self) -> str:
        """Get archive large thumbnail directory."""
        archive_dir = self._archive_dir()
        return os.path.join(archive_dir, 'thumbnails_large') if archive_dir else ''

    def _ensure_thumbnail(self, fname: str, size: int = 160) -> bool:
        """Create thumbnail for a gallery file."""
        return ensure_media_thumbnail(self._gallery_dir(), self._thumb_dir(), fname, size)

    def _ensure_large_thumbnail(self, fname: str, size: int = 600) -> bool:
        """Create large thumbnail for a gallery file."""
        return ensure_media_thumbnail(self._gallery_dir(), self._large_thumb_dir(), fname, size)

    def _ensure_archive_thumbnail(self, fname: str, size: int = 160) -> bool:
        """Create thumbnail for an archive file."""
        return ensure_media_thumbnail(self._archive_dir(), self._archive_thumb_dir(), fname, size)

    def _ensure_archive_large_thumbnail(self, fname: str, size: int = 600) -> bool:
        """Create large thumbnail for an archive file."""
        return ensure_media_thumbnail(
            self._archive_dir(), self._archive_large_thumb_dir(), fname, size
        )

    def _process_categories(self, request, category_enabled: bool = True) -> tuple[list, dict, str, set | None]:
        """Process category operations and filters. Returns (categories, ids_by_file, active_category, filter)."""
        categories = []
        category_ids_by_file = {}
        category_filter = None
        active_category = request.GET.get('category', '').strip()

        if category_enabled:
            try:
                conn = self._gallery_category_db()
                if request.method == 'POST':
                    action = request.POST.get('category_action', '').strip()
                    if action == 'create':
                        name = request.POST.get('name', '').strip()
                        if name:
                            conn.create(name)
                    elif action == 'delete':
                        try:
                            category_id = int(request.POST.get('category_id', ''))
                        except (TypeError, ValueError):
                            category_id = 0
                        if category_id:
                            conn.delete(category_id)
                            if active_category == str(category_id):
                                active_category = ''
                    elif action == 'toggle_file':
                        try:
                            category_id = int(request.POST.get('category_id', ''))
                        except (TypeError, ValueError):
                            category_id = 0
                        filename = os.path.basename(request.POST.get('filename', '').strip())
                        if category_id and filename:
                            state = request.POST.get('state', '').strip()
                            conn.set_file_state(category_id, filename, state)
                categories = self._category_rows(conn)
                category_ids_by_file = self._category_ids_by_file(conn)
                if active_category:
                    if active_category == 'none':
                        category_filter = set()
                    else:
                        try:
                            category_filter = self._category_file_set(conn, int(active_category))
                        except (TypeError, ValueError):
                            active_category = ''
            except Exception:
                pass

        return categories, category_ids_by_file, active_category, category_filter

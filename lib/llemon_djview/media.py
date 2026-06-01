"""llemon_djview.media -- combined LLemon media Django views.

The media app keeps separate image and video creator endpoints, but shares the
gallery and archive pages.

Source directories are read-only image libraries that can be browsed and copied
into the gallery. Configure via input_files / source_thumb_dir in llemon_djview.conf
under [*.llemon.mediagen]; see sourcedirs.py for details.
"""

import json
import mimetypes
import os
import shutil
from datetime import datetime, timezone
from urllib.parse import urlencode

from django.http import FileResponse, Http404, JsonResponse  # type: ignore[import-untyped]
from django.shortcuts import render  # type: ignore[import-untyped]
from django.views.decorators.csrf import csrf_exempt  # type: ignore[import-untyped]
from django.views.decorators.http import require_POST  # type: ignore[import-untyped]

from .imagegen import LLemonImageGenViewSet
from .media_utils import is_image
from .storage import write_operation_sidecar
from .videogen import LLemonVideoGenViewSet


class _MediaNavMixin:
    """Navigation shared by the combined media app."""

    def _media_pages(self):
        items = [
            ('image_creator', 'Image Creator'),
            ('video_creator', 'Video Creator'),
            ('gallery', 'Gallery'),
            ('archive', 'Archive'),
            ('source_dirs', 'Input files'),
        ]
        nav = []
        for route_name, label in items:
            try:
                nav.append({'name': label, 'url': self._u(route_name)})
            except Exception:
                pass
        return nav

    def _media_nav(self):
        nav = self._media_pages()
        return self._nav_prefix + nav

    def _ctx(self, title, nav, extra):
        ctx = {'title': title, 'nav': self._media_nav()}
        if self._base_nav is not None:
            ctx['base_nav'] = self._base_nav
        ctx.update(extra)
        return ctx


class _MediaImageViewSet(_MediaNavMixin, LLemonImageGenViewSet):
    """Image-generation view set with combined media navigation."""


class _MediaVideoViewSet(_MediaNavMixin, LLemonVideoGenViewSet):
    """Video-generation view set with combined media navigation."""

    route_gallery = 'gallery'
    route_archive = 'archive'
    route_video_file = 'image_file'
    route_video_thumbnail = 'thumbnail'
    route_video_large_thumbnail = 'large_thumbnail'
    route_archive_file = 'archive_image_file'
    route_archive_thumbnail = 'archive_thumbnail'
    route_archive_large_thumbnail = 'archive_large_thumbnail'
    route_delete = 'delete_image'
    route_archive_delete = 'delete_archive_image'
    route_upload = 'upload'
    route_move_to_archive = 'move_to_archive'
    route_move_to_gallery = 'move_to_gallery'

    def _nav(self, active=None):
        return self._media_nav()


class LLemonMediaViewSet:
    """Combined image/video media app view set.

    Host URLconfs should map the canonical media page names:

    - ``media`` for the app index
    - ``image_creator`` and ``video_creator`` for the creator pages
    - ``gallery`` and ``archive`` for shared media pages
    """

    def __init__(
        self,
        template_prefix: str = 'llemon_image',
        url_namespace: str = 'llemon_media',
        *,
        video_template_prefix: str = 'llemon_video',
        base_nav=None,
        nav=None,
        nav_suffix=None,
    ):
        self._image = _MediaImageViewSet(
            template_prefix, url_namespace,
            base_nav=base_nav, nav=nav, nav_suffix=nav_suffix,
        )
        self._video = _MediaVideoViewSet(
            video_template_prefix, url_namespace,
            base_nav=base_nav, nav=nav, nav_suffix=nav_suffix,
        )
        self._tp = template_prefix
        self._ns = url_namespace

        self.media = self.index

        self.image_creator = self._image.image_creator
        self.video_creator = self._video.video_creator

        self.gallery = self._image.gallery
        self.archive = self._image.archive
        self.gallery_project_file        = self._image.gallery_project_file
        self.gallery_project_thumb       = self._image.gallery_project_thumb
        self.gallery_project_large_thumb = self._image.gallery_project_large_thumb
        self.gallery_create_project      = self._image.gallery_create_project
        self.gallery_project_move        = self._image.gallery_project_move

        self.source_dirs = self._source_dirs
        self.source_dirs_file = self._source_dirs_file
        self.source_dirs_thumb = self._source_dirs_thumb
        self.source_dirs_large_thumb = self._source_dirs_large_thumb
        self.source_dirs_json = self._source_dirs_json
        self.source_dirs_copy_to_gallery = csrf_exempt(
            require_POST(self._source_dirs_copy_to_gallery)
        )

        self.generate = self._image.generate
        self.video_generate = self._video.generate

        self.model_note = self._image.model_note
        self.video_model_note = self._video.model_note
        self.models_json = self._image.models_json
        self.video_models_json = self._video.models_json

        self.image_file = self._image.image_file
        self.thumbnail = self._image.thumbnail
        self.large_thumbnail = self._image.large_thumbnail
        self.archive_image_file = self._image.archive_image_file
        self.archive_thumbnail = self._image.archive_thumbnail
        self.archive_large_thumbnail = self._image.archive_large_thumbnail

        self.delete_image = self._image.delete_image
        self.delete_archive_image = self._image.delete_archive_image
        self.upload = self._image.upload
        self.upscale = self._image.upscale
        self.upscale_archive = self._image.upscale_archive
        self.edit_image = self._image.edit_image
        self.edit_archive_image = self._image.edit_archive_image
        self.move_to_archive = self._image.move_to_archive
        self.move_to_gallery = self._image.move_to_gallery

    def _u(self, name: str, *args) -> str:
        return self._image._u(name, *args)

    def _t(self, name: str) -> str:
        return self._image._t(name)

    def _media_nav(self):
        return self._image._media_nav()

    def _ctx(self, title: str, nav: list, extra: dict) -> dict:
        return self._image._ctx(title, nav, extra)

    def index(self, request):
        pages = self._image._media_pages()
        return render(request, self._t('index.html'), self._ctx(
            'LLemon Media', pages, {'pages': pages},
        ))

    # ------------------------------------------------------------------ #
    # Source directory browser                                            #
    # ------------------------------------------------------------------ #

    def _source_dirs_json(self, request):
        """Return source dir listing as JSON for the image picker."""
        from django.http import JsonResponse  # type: ignore[import-untyped]
        from .sourcedirs import (
            get_source_dirs, validate_nickname, validate_subdir,
            get_real_path, ensure_source_thumbnail,
        )

        source_dirs_cfg = get_source_dirs()
        nick = request.GET.get('nick', '').strip()
        raw_subdir = request.GET.get('subdir', '').strip()

        if not nick:
            return JsonResponse({
                'type': 'list',
                'source_dirs': [{'name': sd['name'], 'nick': sd['name']} for sd in source_dirs_cfg],
            })

        try:
            sd_entry = validate_nickname(nick, source_dirs_cfg)
            subdir = validate_subdir(raw_subdir)
            current_dir = get_real_path(sd_entry['path'], subdir)
        except ValueError:
            return JsonResponse({'error': 'invalid path'}, status=400)

        if not os.path.isdir(current_dir):
            return JsonResponse({'error': 'not found'}, status=404)

        thumb_base = self._source_thumb_base()

        try:
            entries = sorted(os.listdir(current_dir), key=str.lower)
        except PermissionError:
            entries = []

        dirs = []
        images = []
        for entry in entries:
            if entry.startswith('.'):
                continue
            entry_path = os.path.join(current_dir, entry)
            if os.path.isdir(entry_path):
                child_subdir = f'{subdir}/{entry}' if subdir else entry
                dirs.append({'name': entry, 'nick': nick, 'subdir': child_subdir})
            elif os.path.isfile(entry_path) and is_image(entry):
                rp = f'{subdir}/{entry}' if subdir else entry
                try:
                    file_url = self._u('source_dirs_file', nick, rp)
                    thumb_url = self._u('source_dirs_thumb', nick, rp)
                except Exception:
                    continue
                if thumb_base:
                    ensure_source_thumbnail(current_dir, thumb_base, nick, subdir, entry)
                images.append({'fname': entry, 'url': file_url, 'thumb_url': thumb_url})

        return JsonResponse({
            'type': 'dir',
            'nick': nick,
            'subdir': subdir,
            'dirs': dirs,
            'images': images,
        })

    def _source_thumb_base(self) -> str:
        from .sourcedirs import source_thumb_base
        return source_thumb_base()

    def _source_dirs(self, request):
        from .sourcedirs import (
            get_source_dirs, validate_nickname, validate_subdir,
            get_real_path, ensure_source_thumbnail, ensure_source_large_thumbnail,
        )

        source_dirs_cfg = get_source_dirs()
        nick = request.GET.get('nick', '').strip()
        raw_subdir = request.GET.get('subdir', '').strip()

        try:
            base_url = self._u('source_dirs')
        except Exception:
            base_url = ''

        def _browse_url(n: str, sd: str = '') -> str:
            params: dict = {'nick': n}
            if sd:
                params['subdir'] = sd
            return base_url + '?' + urlencode(params)

        if not nick:
            items = []
            for sd in source_dirs_cfg:
                name = sd.get('name', '')
                if not name:
                    continue
                items.append({'name': name, 'url': _browse_url(name)})
            return render(request, self._t('source_dirs.html'), self._ctx(
                'Source Dirs', [], {
                    'mode': 'list',
                    'source_dirs': items,
                    'source_dirs_url': base_url,
                },
            ))

        try:
            sd_entry = validate_nickname(nick, source_dirs_cfg)
            subdir = validate_subdir(raw_subdir)
            current_dir = get_real_path(sd_entry['path'], subdir)
        except ValueError:
            raise Http404

        if not os.path.isdir(current_dir):
            raise Http404

        thumb_base = self._source_thumb_base()

        # Build breadcrumb segments
        parts = subdir.split('/') if subdir else []
        breadcrumb = []
        for i, part in enumerate(parts):
            parent_subdir = '/'.join(parts[:i + 1])
            breadcrumb.append({'name': part, 'url': _browse_url(nick, parent_subdir)})

        # Parent directory URL
        if parts:
            parent_url = _browse_url(nick, '/'.join(parts[:-1]))
        else:
            parent_url = base_url

        # List directory contents
        try:
            entries = sorted(os.listdir(current_dir), key=str.lower)
        except PermissionError:
            entries = []

        subdirs_list = []
        images = []
        for entry in entries:
            if entry.startswith('.'):
                continue
            entry_path = os.path.join(current_dir, entry)
            if os.path.isdir(entry_path):
                child_subdir = f'{subdir}/{entry}' if subdir else entry
                subdirs_list.append({'name': entry, 'url': _browse_url(nick, child_subdir)})
            elif os.path.isfile(entry_path) and is_image(entry):
                rp = f'{subdir}/{entry}' if subdir else entry
                try:
                    file_url = self._u('source_dirs_file', nick, rp)
                    thumb_url = self._u('source_dirs_thumb', nick, rp)
                except Exception:
                    continue
                large_thumb_url = ''
                if thumb_base:
                    ensure_source_thumbnail(current_dir, thumb_base, nick, subdir, entry)
                    if ensure_source_large_thumbnail(current_dir, thumb_base, nick, subdir, entry):
                        try:
                            large_thumb_url = self._u('source_dirs_large_thumb', nick, rp)
                        except Exception:
                            pass
                images.append({
                    'fname': entry,
                    'rp': rp,
                    'url': file_url,
                    'thumb_url': thumb_url,
                    'large_thumb_url': large_thumb_url,
                })

        try:
            copy_url = self._u('source_dirs_copy_to_gallery')
        except Exception:
            copy_url = ''

        return render(request, self._t('source_dirs.html'), self._ctx(
            f'Source: {nick}', [], {
                'mode': 'browse',
                'nick': nick,
                'subdir': subdir,
                'breadcrumb': breadcrumb,
                'parent_url': parent_url,
                'subdirs': subdirs_list,
                'images': images,
                'source_dirs_url': base_url,
                'copy_to_gallery_url': copy_url,
            },
        ))

    def _source_dirs_file(self, request, nick: str, rp: str):
        from .sourcedirs import (
            get_source_dirs, validate_nickname, validate_subdir,
            safe_source_filename, get_real_path,
        )

        source_dirs_cfg = get_source_dirs()
        try:
            sd_entry = validate_nickname(nick, source_dirs_cfg)
        except ValueError:
            raise Http404

        if '/' in rp:
            subdir_part, fname = rp.rsplit('/', 1)
        else:
            subdir_part, fname = '', rp

        try:
            subdir_part = validate_subdir(subdir_part)
            fname = safe_source_filename(fname)
            file_path = get_real_path(sd_entry['path'], subdir_part, fname)
        except ValueError:
            raise Http404

        if not os.path.isfile(file_path):
            raise Http404

        mime, _ = mimetypes.guess_type(fname)
        return FileResponse(open(file_path, 'rb'), content_type=mime or 'application/octet-stream')

    def _source_dirs_thumb(self, request, nick: str, rp: str):
        from .sourcedirs import (
            get_source_dirs, validate_nickname, validate_subdir,
            safe_source_filename, get_real_path, ensure_source_thumbnail, source_thumb_dir,
        )

        source_dirs_cfg = get_source_dirs()
        try:
            sd_entry = validate_nickname(nick, source_dirs_cfg)
        except ValueError:
            raise Http404

        if '/' in rp:
            subdir_part, fname = rp.rsplit('/', 1)
        else:
            subdir_part, fname = '', rp

        try:
            subdir_part = validate_subdir(subdir_part)
            fname = safe_source_filename(fname)
            current_dir = get_real_path(sd_entry['path'], subdir_part)
        except ValueError:
            raise Http404

        thumb_base = self._source_thumb_base()
        if not thumb_base:
            raise Http404

        if not ensure_source_thumbnail(current_dir, thumb_base, nick, subdir_part, fname):
            raise Http404

        t_dir = source_thumb_dir(thumb_base, nick, subdir_part)
        thumb_path = os.path.join(t_dir, fname)
        if not os.path.isfile(thumb_path):
            raise Http404

        mime, _ = mimetypes.guess_type(thumb_path)
        return FileResponse(open(thumb_path, 'rb'), content_type=mime or 'image/jpeg')

    def _source_dirs_large_thumb(self, request, nick: str, rp: str):
        from .sourcedirs import (
            get_source_dirs, validate_nickname, validate_subdir,
            safe_source_filename, get_real_path, ensure_source_large_thumbnail,
            source_large_thumb_dir,
        )

        source_dirs_cfg = get_source_dirs()
        try:
            sd_entry = validate_nickname(nick, source_dirs_cfg)
        except ValueError:
            raise Http404

        if '/' in rp:
            subdir_part, fname = rp.rsplit('/', 1)
        else:
            subdir_part, fname = '', rp

        try:
            subdir_part = validate_subdir(subdir_part)
            fname = safe_source_filename(fname)
            current_dir = get_real_path(sd_entry['path'], subdir_part)
        except ValueError:
            raise Http404

        thumb_base = self._source_thumb_base()
        if not thumb_base:
            raise Http404

        if not ensure_source_large_thumbnail(current_dir, thumb_base, nick, subdir_part, fname):
            raise Http404

        t_dir = source_large_thumb_dir(thumb_base, nick, subdir_part)
        thumb_path = os.path.join(t_dir, fname)
        if not os.path.isfile(thumb_path):
            raise Http404

        mime, _ = mimetypes.guess_type(thumb_path)
        return FileResponse(open(thumb_path, 'rb'), content_type=mime or 'image/jpeg')

    def _source_dirs_copy_to_gallery(self, request):
        """Copy a source directory image into the gallery (read-only source is never modified)."""
        from .sourcedirs import (
            get_source_dirs, validate_nickname, validate_subdir,
            safe_source_filename, get_real_path,
        )
        from .storage import sidecar_path

        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)

        nick = (data.get('nick') or '').strip()
        rp = (data.get('rp') or '').strip()

        source_dirs_cfg = get_source_dirs()
        try:
            sd_entry = validate_nickname(nick, source_dirs_cfg)
        except ValueError:
            return JsonResponse({'error': 'invalid source directory'}, status=400)

        if '/' in rp:
            subdir_part, fname = rp.rsplit('/', 1)
        else:
            subdir_part, fname = '', rp

        try:
            subdir_part = validate_subdir(subdir_part)
            fname = safe_source_filename(fname)
            file_path = get_real_path(sd_entry['path'], subdir_part, fname)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)

        if not os.path.isfile(file_path):
            return JsonResponse({'error': 'file not found'}, status=404)

        gallery_dir = self._image._gallery_dir()
        if not gallery_dir:
            return JsonResponse({'error': 'gallery not configured'}, status=500)

        dest_fname = fname
        dest_path = os.path.join(gallery_dir, dest_fname)
        if os.path.exists(dest_path):
            stem, ext = os.path.splitext(dest_fname)
            i = 1
            while os.path.exists(dest_path):
                dest_fname = f'{stem}_{i}{ext}'
                dest_path = os.path.join(gallery_dir, dest_fname)
                i += 1

        try:
            os.makedirs(gallery_dir, exist_ok=True)
            shutil.copy2(file_path, dest_path)
        except OSError as e:
            return JsonResponse({'error': f'could not copy file: {e}'}, status=500)

        # Load source sidecar as the starting point, then add copy provenance.
        payload: dict = {}
        src_sidecar = sidecar_path(file_path)
        if os.path.isfile(src_sidecar):
            try:
                with open(src_sidecar, encoding='utf-8') as f:
                    payload = json.load(f)
                if not isinstance(payload, dict):
                    payload = {}
            except Exception:
                payload = {}

        timestamp = datetime.now(timezone.utc).isoformat()
        payload.update({
            'source': 'source_dir',
            'source_nick': nick,
            'source_rp': rp,
            'timestamp': timestamp,
            'files': [dest_fname],
        })
        try:
            write_operation_sidecar(dest_path, payload)
        except OSError:
            pass

        return JsonResponse({'file': dest_fname})


def bind_llemon_views(namespace: dict, persona_viewset, media_viewset) -> None:
    """Bind persona and media viewset callables into a thin Django views module."""
    namespace.update({
        'persona_index': persona_viewset.index,
        'persona_session': persona_viewset.session,
        'persona_configs': persona_viewset.configs,
        'persona_chat': persona_viewset.chat,
        'persona_stream': persona_viewset.stream,
        'persona_system': persona_viewset.system,
        'persona_service': persona_viewset.service,
        'persona_services': persona_viewset.services,
        'persona_models': persona_viewset.models,
        'persona_render_markdown': persona_viewset.render_markdown,
        'persona_edit_history': persona_viewset.edit_history,
        'persona_delete_history': persona_viewset.delete_history,
        'persona_set_history_name': persona_viewset.set_history_name,
        'persona_set_history_title': persona_viewset.set_history_title,

        'media': media_viewset.media,
        'image_creator': media_viewset.image_creator,
        'video_creator': media_viewset.video_creator,
        'gallery': media_viewset.gallery,
        'archive': media_viewset.archive,

        'generate': media_viewset.generate,
        'image_file': media_viewset.image_file,
        'thumbnail': media_viewset.thumbnail,
        'large_thumbnail': media_viewset.large_thumbnail,
        'model_note': media_viewset.model_note,
        'models_json': media_viewset.models_json,
        'delete_image': media_viewset.delete_image,
        'upscale': media_viewset.upscale,
        'edit_image': media_viewset.edit_image,
        'upload': media_viewset.upload,
        'archive_image_file': media_viewset.archive_image_file,
        'archive_thumbnail': media_viewset.archive_thumbnail,
        'archive_large_thumbnail': media_viewset.archive_large_thumbnail,
        'delete_archive_image': media_viewset.delete_archive_image,
        'upscale_archive': media_viewset.upscale_archive,
        'edit_archive_image': media_viewset.edit_archive_image,
        'move_to_archive': media_viewset.move_to_archive,
        'move_to_gallery': media_viewset.move_to_gallery,

        'video_generate': media_viewset.video_generate,
        'video_model_note': media_viewset.video_model_note,
        'video_models_json': media_viewset.video_models_json,

        'gallery_project_file':        media_viewset.gallery_project_file,
        'gallery_project_thumb':       media_viewset.gallery_project_thumb,
        'gallery_project_large_thumb': media_viewset.gallery_project_large_thumb,
        'gallery_create_project':      media_viewset.gallery_create_project,
        'gallery_project_move':        media_viewset.gallery_project_move,

        'source_dirs': media_viewset.source_dirs,
        'source_dirs_file': media_viewset.source_dirs_file,
        'source_dirs_thumb': media_viewset.source_dirs_thumb,
        'source_dirs_large_thumb': media_viewset.source_dirs_large_thumb,
        'source_dirs_json': media_viewset.source_dirs_json,
        'source_dirs_copy_to_gallery': media_viewset.source_dirs_copy_to_gallery,
    })


def media_urlpatterns(views_module):
    """Return canonical Media app URL patterns for a thin Django frontend."""
    from django.urls import path  # type: ignore[import-untyped]

    return [
        path('', views_module.index, name='index'),
        path('media/', views_module.media, name='media'),
        path('media/image-creator/', views_module.image_creator, name='image_creator'),
        path('media/video-creator/', views_module.video_creator, name='video_creator'),
        path('media/gallery/', views_module.gallery, name='gallery'),
        path('media/archive/', views_module.archive, name='archive'),

        path('media/image/generate/', views_module.generate, name='generate'),
        path('media/image/file/<str:filename>', views_module.image_file, name='image_file'),
        path('media/image/thumbnails/<str:filename>', views_module.thumbnail, name='thumbnail'),
        path(
            'media/image/thumbnails-large/<str:filename>',
            views_module.large_thumbnail,
            name='large_thumbnail',
        ),
        path('media/image/model-note/', views_module.model_note, name='model_note'),
        path('media/image/models-json/', views_module.models_json, name='models_json'),
        path('media/image/delete/', views_module.delete_image, name='delete_image'),
        path('media/image/upscale/', views_module.upscale, name='upscale'),
        path('media/image/edit/', views_module.edit_image, name='edit_image'),
        path('media/gallery/upload/', views_module.upload, name='upload'),
        path(
            'media/archive/file/<str:filename>',
            views_module.archive_image_file,
            name='archive_image_file',
        ),
        path(
            'media/archive/thumbnails/<str:filename>',
            views_module.archive_thumbnail,
            name='archive_thumbnail',
        ),
        path(
            'media/archive/thumbnails-large/<str:filename>',
            views_module.archive_large_thumbnail,
            name='archive_large_thumbnail',
        ),
        path(
            'media/archive/delete/',
            views_module.delete_archive_image,
            name='delete_archive_image',
        ),
        path('media/archive/upscale/', views_module.upscale_archive, name='upscale_archive'),
        path('media/archive/edit/', views_module.edit_archive_image, name='edit_archive_image'),
        path('media/move-to-archive/', views_module.move_to_archive, name='move_to_archive'),
        path('media/move-to-gallery/', views_module.move_to_gallery, name='move_to_gallery'),

        path('media/video/generate/', views_module.video_generate, name='video_generate'),
        path('media/video/model-note/', views_module.video_model_note, name='video_model_note'),
        path('media/video/models-json/', views_module.video_models_json, name='video_models_json'),

        path(
            'media/gallery/project-file/<path:subpath>',
            views_module.gallery_project_file,
            name='gallery_project_file',
        ),
        path(
            'media/gallery/project-thumb/<path:subpath>',
            views_module.gallery_project_thumb,
            name='gallery_project_thumb',
        ),
        path(
            'media/gallery/project-thumb-large/<path:subpath>',
            views_module.gallery_project_large_thumb,
            name='gallery_project_large_thumb',
        ),
        path('media/gallery/create-project/', views_module.gallery_create_project, name='gallery_create_project'),
        path('media/gallery/project-move/', views_module.gallery_project_move, name='gallery_project_move'),

        path('media/source-dirs/', views_module.source_dirs, name='source_dirs'),
        path('media/source-dirs/json/', views_module.source_dirs_json, name='source_dirs_json'),
        path(
            'media/source-dirs/file/<str:nick>/<path:rp>',
            views_module.source_dirs_file,
            name='source_dirs_file',
        ),
        path(
            'media/source-dirs/thumb/<str:nick>/<path:rp>',
            views_module.source_dirs_thumb,
            name='source_dirs_thumb',
        ),
        path(
            'media/source-dirs/thumb-large/<str:nick>/<path:rp>',
            views_module.source_dirs_large_thumb,
            name='source_dirs_large_thumb',
        ),
        path(
            'media/source-dirs/copy-to-gallery/',
            views_module.source_dirs_copy_to_gallery,
            name='source_dirs_copy_to_gallery',
        ),
    ]

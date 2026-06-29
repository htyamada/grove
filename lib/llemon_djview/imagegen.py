"""llemon_djview.imagegen - Django view logic for LLemon image generation.

Each app instantiates LLemonImageGenViewSet with its own template prefix and URL namespace.
"""

import base64
import json
import logging
import mimetypes
import os
import queue
import re
import threading
from datetime import datetime, timezone
from urllib.parse import urlencode
from typing import Any

from django.conf import settings  # type: ignore[import-untyped]
from django.http import FileResponse, Http404, JsonResponse, StreamingHttpResponse  # type: ignore[import-untyped]
from django.shortcuts import render  # type: ignore[import-untyped]
from django.views.decorators.csrf import csrf_exempt  # type: ignore[import-untyped]
from django.views.decorators.http import require_POST  # type: ignore[import-untyped]

from hty7.llemon.mediagen.imagegen import (
    aspect_ratios,
    default_aspect_ratio,
    default_edit_model,
    default_image_model,
    default_image_size,
    default_system_prompt,
    edit_aspect_ratios,
    edit_models,
    extract_extra_params,
    get_model_note,
    get_model_tag_states,
    get_notes_load_errors,
    get_notes_slot,
    get_reverse_tags,
    get_tags,
    set_model_note,
    image_sizes,
    image_generation_summary_lines,
    list_image_models_with_metadata,
    make_imagegen_backend,
    model_capabilities,
    model_display,
    model_quirk_labels,
    normalize_provider_api,
    provider_config as _provider_config,
    supports_edit,
    supports_upscale,
    PROVIDERS,
    write_image_metadata,
    LLemonImageParamError,
)
from .storage import (
    VIDEO_EXTS,
    delete_image_asset,
    delete_video_asset,
    image_as_data_url,
    move_image_asset,
    move_video_asset,
    read_image_sidecar,
    save_operation_images,
    save_uploaded_image_files,
    sanitize_metadata_data_urls,
    video_thumb_name,
    write_operation_sidecar,
)
from .media_utils import ensure_media_thumbnail, is_video
from .base_viewset import MediaGenViewSetBase, _RESERVED_GALLERY_DIRS

logger = logging.getLogger(__name__)


_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
_MEDIA_EXTS = _IMAGE_EXTS | VIDEO_EXTS


def _sanitize_image_metadata(value: Any) -> Any:
    return sanitize_metadata_data_urls(value)


class LLemonImageGenViewSet(MediaGenViewSetBase):
    """Django views for LLemon image generation, bound to a specific app namespace."""

    def __init__(self, template_prefix: str, url_namespace: str, *, base_nav=None,
                 nav=None, nav_suffix=None):
        super().__init__(
            template_prefix, url_namespace,
            base_nav=base_nav, nav=nav, nav_suffix=nav_suffix,
        )
        self.gallery             = csrf_exempt(self.gallery)
        self.large_thumbnail     = self._large_thumbnail
        self.generate            = csrf_exempt(require_POST(self._generate))
        self.model_note          = csrf_exempt(self._model_note)
        self.delete_image        = csrf_exempt(require_POST(self._delete_image))
        self.models_json         = self._models_json
        self.upscale             = csrf_exempt(require_POST(self._upscale))
        self.edit_image          = csrf_exempt(require_POST(self._edit_image))
        self.upload              = csrf_exempt(require_POST(self._upload))
        self.archive_image_file        = self._archive_image_file
        self.archive_thumbnail         = self._archive_thumbnail
        self.archive_large_thumbnail   = self._archive_large_thumbnail
        self.delete_archive_image = csrf_exempt(require_POST(self._delete_archive_image))
        self.upscale_archive     = csrf_exempt(require_POST(self._upscale_archive))
        self.edit_archive_image  = csrf_exempt(require_POST(self._edit_archive_image))
        self.move_to_archive     = csrf_exempt(require_POST(self._move_to_archive))
        self.move_to_gallery     = csrf_exempt(require_POST(self._move_to_gallery))
        self.gallery_project_file       = self._gallery_project_file
        self.gallery_project_thumb      = self._gallery_project_thumb
        self.gallery_project_large_thumb = self._gallery_project_large_thumb
        self.gallery_create_project     = csrf_exempt(require_POST(self._gallery_create_project))
        self.gallery_project_move       = csrf_exempt(require_POST(self._gallery_project_move))

    def _media_dir(self):
        return getattr(settings, 'LLEMON_IMAGEGEN_MEDIA_DIR', '')

    @staticmethod
    def _safe_image_name(filename: str) -> str:
        fname = filename.strip()
        if not fname or '/' in fname or fname.startswith('.'):
            raise ValueError('invalid filename')
        if os.path.splitext(fname)[1].lower() not in _IMAGE_EXTS:
            raise ValueError('unsupported image format')
        return fname

    @staticmethod
    def _safe_filename(filename: str) -> str:
        fname = filename.strip()
        if not fname or '/' in fname or fname.startswith('.'):
            raise ValueError('invalid filename')
        if os.path.splitext(fname)[1].lower() not in _MEDIA_EXTS:
            raise ValueError('unsupported media format')
        return fname

    def _log_dir(self):
        return getattr(settings, 'LLEMON_IMAGEGEN_LOG_DIR', None)

    def _source_dirs_json_url(self) -> str:
        return ''

    def _gallery_picker_items(self) -> list[dict]:
        """Return a compact list of gallery images for creator source-image pickers."""
        gallery_dir = self._gallery_dir()
        if not gallery_dir or not os.path.isdir(gallery_dir):
            return []
        items = []
        thumb_dir = self._thumb_dir()
        for fname in sorted(os.listdir(gallery_dir), reverse=True):
            if os.path.splitext(fname)[1].lower() not in _IMAGE_EXTS:
                continue
            has_thumb = bool(thumb_dir) and os.path.isfile(os.path.join(thumb_dir, fname))
            try:
                url = self._u('image_file', fname)
                thumb_url = self._u('thumbnail', fname) if has_thumb else url
            except Exception:
                continue
            items.append({
                'fname':     fname,
                'url':       url,
                'thumb_url': thumb_url,
            })
        return items

    # ------------------------------------------------------------------ #

    def image(self, request):
        def _safe_url(name: str) -> str | None:
            try:
                return self._u(name)
            except Exception:
                return None

        pages = [
            {'name': 'Image creator', 'url': self._u('image_creator')},
            {'name': 'Gallery', 'url': self._u('gallery')},
        ]
        return render(request, self._t('index.html'), self._ctx(
            'LLemon Image', pages, {'pages': pages},
        ))

    def image_creator(self, request):
        provider_param = request.GET.get('provider', '').strip() or None
        try:
            provider, api = normalize_provider_api(provider_param)
            raw_models = list_image_models_with_metadata(provider, api)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
            logger.exception('could not list image generation models')
            return JsonResponse({'error': f'could not list image generation models: {e}'},
                                status=502)
        model_options = []
        model_descriptions: dict[str, str] = {}
        model_quirks: dict[str, list[str]] = {}
        model_system_prompts: dict[str, str] = {}
        model_qualities: dict[str, dict] = {}
        for m in raw_models:
            mid  = m['id']
            name = m['name']
            model_options.append({
                'id':      mid,
                'display': f'{name} ({mid})' if name else mid,
            })
            model_descriptions[mid] = m['description']
            quirks = model_quirk_labels(mid, provider, api)
            if quirks:
                model_quirks[mid] = quirks
            system_prompt = default_system_prompt(mid, provider, api)
            if system_prompt is not None:
                model_system_prompts[mid] = system_prompt
            try:
                caps = model_capabilities(mid, provider, api)
                quals = caps.get('qualities') or []
                if quals:
                    model_qualities[mid] = {
                        'qualities': quals,
                        'default': caps.get('default_quality'),
                    }
            except Exception:
                pass
        model_tag_states = self._model_tag_states(
            provider, [opt['id'] for opt in model_options],
        )

        notes_load_errors = get_notes_load_errors()
        def _safe_url(name: str) -> str | None:
            try:
                return self._u(name)
            except Exception:
                return None

        output_subdir_raw = request.GET.get('output_subdir', '').strip()
        try:
            output_subdir = self._safe_subdir(output_subdir_raw)
        except ValueError:
            output_subdir = ''
        if output_subdir:
            gallery_dir_c = self._gallery_dir()
            if not gallery_dir_c or not self._validated_project_dir(gallery_dir_c, output_subdir):
                output_subdir = ''
        if output_subdir:
            image_file_url = self._u('gallery_project_file', f'{output_subdir}/PLACEHOLDER')
            large_thumbnail_file_url = self._u('gallery_project_large_thumb', f'{output_subdir}/PLACEHOLDER')
            creator_self_url = self._u('image_creator') + '?' + urlencode({'output_subdir': output_subdir})
            gallery_back_url = self._u('gallery') + '?' + urlencode({'subdir': output_subdir})
        else:
            image_file_url = self._u('image_file', 'PLACEHOLDER')
            large_thumbnail_file_url = self._u('large_thumbnail', 'PLACEHOLDER')
            creator_self_url = self._u('image_creator')
            gallery_back_url = self._u('gallery')

        nav = [{'name': 'Image creator', 'url': creator_self_url},
               {'name': 'Gallery', 'url': gallery_back_url}]
        video_creator_url = _safe_url('video_creator')
        if video_creator_url and output_subdir:
            nav.append({
                'name': 'Video Creator',
                'url': video_creator_url + '?' + urlencode({'output_subdir': output_subdir}),
            })
        try:
            nav.append({'name': 'Archive', 'url': self._u('archive')})
        except Exception:
            pass
        source_dirs_url = _safe_url('source_dirs')
        if source_dirs_url and output_subdir:
            nav.append({
                'name': 'Input files',
                'url': source_dirs_url + '?' + urlencode({'dest_subdir': output_subdir}),
            })

        return render(request, self._t('image.html'), self._ctx(
            'LLemon Image Creator', nav, {
                'providers':          PROVIDERS,
                'provider':           provider,
                'api':                api,
                'aspect_ratios':      aspect_ratios(provider, api),
                'image_sizes':        image_sizes(provider, api),
                'default_aspect_ratio': default_aspect_ratio(provider, api),
                'default_image_size': default_image_size(provider, api),
                'default_model':      default_image_model(provider, api),
                'model_options':      model_options,
                'model_tag_states':   model_tag_states,
                'model_descriptions': model_descriptions,
                'model_quirks':       model_quirks,
                'model_system_prompts': model_system_prompts,
                'model_qualities':    model_qualities,
                'provider_config':    _provider_config(provider, api),
                'available_tags':      [] if notes_load_errors else get_tags(),
                'reverse_tags':        [] if notes_load_errors else get_reverse_tags(),
                'notes_load_errors':   notes_load_errors,
                'active_notes_slot':   get_notes_slot(),
                'output_subdir':       output_subdir,
                'generate_url':            self._u('generate'),
                'image_file_url':          image_file_url,
                'large_thumbnail_file_url': large_thumbnail_file_url,
                'model_note_url':          self._u('model_note'),
                'models_json_url':    self._u('models_json'),
                'upscale_url':              _safe_url('upscale'),
                'edit_image_url':           _safe_url('edit_image'),
                'picker_images':            self._gallery_picker_items(),
                'source_dirs_json_url':     self._source_dirs_json_url(),
                'supports_edit':            supports_edit(provider, api),
                'supports_upscale':         supports_upscale(provider, api),
                'edit_models':              edit_models(provider, api),
                'default_edit_model':       default_edit_model(provider, api),
                'edit_aspect_ratios':       ([''] + edit_aspect_ratios(provider, api)
                                             if edit_aspect_ratios(provider, api) else []),
            },
        ))

    def _find_sidecar(self, media_dir: str, fname: str) -> 'dict | None':
        return read_image_sidecar(media_dir, fname, _sanitize_image_metadata)

    def gallery(self, request):
        gallery_dir = self._gallery_dir()

        raw_subdir = request.GET.get('subdir', '').strip()
        try:
            subdir = self._safe_subdir(raw_subdir)
        except ValueError:
            raise Http404

        categories, category_ids_by_file, active_category, category_filter = \
            self._process_categories(request, category_enabled=(not subdir))

        if subdir:
            current_dir = self._validated_project_dir(gallery_dir, subdir)
            if not current_dir:
                raise Http404
        else:
            current_dir = gallery_dir

        try:
            gallery_base_url = self._u('gallery')
        except Exception:
            gallery_base_url = ''
        active_gallery_url = (
            gallery_base_url + '?' + urlencode({'subdir': subdir})
            if subdir else gallery_base_url
        )

        parts = subdir.split('/') if subdir else []
        breadcrumb = [
            {'name': part, 'url': gallery_base_url + '?' + urlencode({'subdir': '/'.join(parts[:i + 1])})}
            for i, part in enumerate(parts)
        ]
        if parts:
            parent_subdir = '/'.join(parts[:-1])
            parent_url = (gallery_base_url + '?' + urlencode({'subdir': parent_subdir})
                          if parent_subdir else gallery_base_url)
        else:
            parent_subdir = ''
            parent_url = None

        subdirs_list: list[dict] = []
        items: list[dict] = []
        if current_dir and os.path.isdir(current_dir):
            for entry in sorted(os.listdir(current_dir), reverse=True):
                if entry.startswith('.') or entry in _RESERVED_GALLERY_DIRS:
                    continue
                entry_path = os.path.join(current_dir, entry)
                if os.path.isdir(entry_path):
                    entry_subdir = f'{subdir}/{entry}' if subdir else entry
                    subdirs_list.append({
                        'name': entry,
                        'subdir': entry_subdir,
                        'url': gallery_base_url + '?' + urlencode({'subdir': entry_subdir}),
                    })
                    continue
                if not os.path.isfile(entry_path):
                    continue
                if os.path.splitext(entry)[1].lower() not in _MEDIA_EXTS:
                    continue
                if not subdir:
                    if active_category == 'none' and category_ids_by_file.get(entry):
                        continue
                    if active_category != 'none' and category_filter is not None and entry not in category_filter:
                        continue
                try:
                    if subdir:
                        rp = f'{subdir}/{entry}'
                        file_url = self._u('gallery_project_file', rp)
                        thumb_url = self._u('gallery_project_thumb', rp)
                        large_thumb_url = self._u('gallery_project_large_thumb', rp)
                        ensure_media_thumbnail(current_dir, self._thumb_dir(current_dir), entry, 160)
                        ensure_media_thumbnail(current_dir, self._large_thumb_dir(current_dir), entry, 600)
                    else:
                        file_url = self._u('image_file', entry)
                        thumb_url = self._u('thumbnail', entry)
                        large_thumb_url = self._u('large_thumbnail', entry)
                        self._ensure_thumbnail(entry)
                        self._ensure_large_thumbnail(entry)
                except Exception:
                    continue
                items.append({
                    'fname':           entry,
                    'subdir':          subdir,
                    'type':            'video' if is_video(entry) else 'image',
                    'url':             file_url,
                    'thumb_url':       thumb_url,
                    'large_thumb_url': large_thumb_url,
                    'sidecar':         self._find_sidecar(current_dir, entry),
                    'category_ids':    sorted(category_ids_by_file.get(entry, [])) if not subdir else [],
                })

        subdirs_list.sort(key=lambda d: d['name'].lower())

        def _safe_url(name: str) -> str | None:
            try:
                return self._u(name)
            except Exception:
                return None

        def _creator_url(name: str) -> str:
            base = self._u(name)
            return (base + '?' + urlencode({'output_subdir': subdir})) if subdir else base

        nav = [{'name': 'Image creator', 'url': _creator_url('image_creator')},
               {'name': 'Gallery', 'url': active_gallery_url}]
        video_creator_url = _safe_url('video_creator')
        if video_creator_url:
            nav.append({'name': 'Video Creator', 'url': _creator_url('video_creator')})
        source_dirs_url = _safe_url('source_dirs')
        if source_dirs_url:
            if subdir:
                source_dirs_url += '?' + urlencode({'dest_subdir': subdir})
            nav.append({'name': 'Input files', 'url': source_dirs_url})
        return render(request, self._t('gallery.html'), self._ctx(
            'LLemon Image Gallery', nav, {
                'images':                    items,
                'subdirs':                   subdirs_list,
                'subdir':                    subdir,
                'parent_url':                parent_url,
                'parent_subdir':             parent_subdir,
                'breadcrumb':                breadcrumb,
                'gallery_url':               gallery_base_url,
                'generate_url':              _creator_url('image_creator'),
                'video_generate_url':        _creator_url('video_creator') if _safe_url('video_creator') else None,
                'upload_url':                _safe_url('upload'),
                'delete_image_url':          self._u('delete_image'),
                'move_to_archive_url':       _safe_url('move_to_archive'),
                'gallery_project_move_url':  _safe_url('gallery_project_move'),
                'gallery_create_project_url': _safe_url('gallery_create_project'),
                'category_enabled':          not subdir,
                'categories':                categories,
                'active_category':           active_category,
            },
        ))

    def archive(self, request):
        archive_dir = self._archive_dir()
        items = []
        if archive_dir and os.path.isdir(archive_dir):
            for fname in sorted(os.listdir(archive_dir), reverse=True):
                if os.path.splitext(fname)[1].lower() not in _MEDIA_EXTS:
                    continue
                has_thumb = self._ensure_archive_thumbnail(fname)
                has_large_thumb = self._ensure_archive_large_thumbnail(fname)
                items.append({
                    'fname':           fname,
                    'type':            'video' if is_video(fname) else 'image',
                    'url':             self._u('archive_image_file', fname),
                    'thumb_url':       self._u('archive_thumbnail', fname),
                    'large_thumb_url': self._u('archive_large_thumbnail', fname),
                    'sidecar':         self._find_sidecar(archive_dir, fname),
                })

        def _safe_url(name: str) -> str | None:
            try:
                return self._u(name)
            except Exception:
                return None

        nav = [{'name': 'Image creator', 'url': self._u('image_creator')},
               {'name': 'Gallery', 'url': self._u('gallery')}]
        archive_url = _safe_url('archive')
        if archive_url:
            nav.append({'name': 'Archive', 'url': archive_url})

        return render(request, self._t('archive.html'), self._ctx(
            'LLemon Image Archive', nav, {
                'images':             items,
                'video_generate_url': _safe_url('video_creator'),
                'delete_image_url':   self._u('delete_archive_image'),
                'move_to_gallery_url': _safe_url('move_to_gallery'),
                'empty':              'No archived images yet.',
            },
        ))

    def _generate_result(
        self,
        prompt: str,
        model: str,
        aspect_ratio: str,
        image_size: str,
        temperature: float | None,
        temperature_force: float | None,
        system: str | None,
        system_force: str | None,
        provider: str,
        api: str,
        extra_params: dict[str, Any] | None = None,
        output_subdir: str = '',
    ) -> tuple[dict[str, Any], int]:
        gallery_dir = self._gallery_dir()
        if output_subdir:
            save_dir = self._validated_project_dir(gallery_dir, output_subdir)
            if not save_dir:
                return {'error': 'invalid output_subdir'}, 400
        else:
            save_dir = gallery_dir
        try:
            backend_cls = make_imagegen_backend(provider, api)
            backend = backend_cls(model=model, log_dir=self._log_dir())
        except Exception as e:
            logger.exception('could not create imagegen backend')
            return {'error': str(e)}, 500

        try:
            result = backend.generate(
                prompt,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                temperature=temperature,
                temperature_force=temperature_force,
                system=system,
                system_force=system_force,
                **(extra_params or {}),
            )
        finally:
            backend.shutdown()

        if result.get('error'):
            err = result['error']
            status = 400 if err.get('type') == 'unsupported_temperature' else 502
            return {'error': err['message']}, status

        images = result.get('images', [])
        if not images:
            return {'error': 'no images returned'}, 502

        try:
            files, desc_file = save_operation_images(
                backend_cls.write_images,
                images,
                save_dir,
                datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'),
            )
        except Exception as e:
            logger.exception('could not write images')
            return {'error': f'could not write image: {e}'}, 500

        usage = result.get('usage') or {}
        cost  = usage.get('cost')
        for _fname in files:
            if output_subdir and save_dir:
                ensure_media_thumbnail(save_dir, self._large_thumb_dir(save_dir), _fname, 600)
            else:
                self._ensure_large_thumbnail(_fname)
        actual_model = result.get('model') or model
        metadata_system = system if system is not None else result.get('system')
        metadata_system_force = (
            system_force if system_force is not None else result.get('system_force')
        )
        if metadata_system_force is not None:
            metadata_system = None

        try:
            write_image_metadata(
                desc_file,
                model=actual_model,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                cost=cost,
                files=files,
                prompt=prompt,
                system=metadata_system,
                system_force=metadata_system_force,
                temperature=temperature,
                temperature_force=temperature_force,
                provider=provider,
                api=api,
                extra_params=_sanitize_image_metadata(extra_params) or None,
            )
        except OSError as e:
            logger.warning('could not write metadata file: %s', e)

        summary = image_generation_summary_lines(
            provider=provider,
            model=model_display(actual_model, provider, api),
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            cost=cost,
            file=files[0] if files else '',
            prompt=prompt,
        )

        return {
            'files':         files,
            'cost':          cost,
            'model':         actual_model,
            'model_display': model_display(actual_model),
            'summary':       summary,
        }, 200

    def _generate_stream(
        self,
        prompt: str,
        model: str,
        aspect_ratio: str,
        image_size: str,
        temperature: float | None,
        temperature_force: float | None,
        system: str | None,
        system_force: str | None,
        provider: str,
        api: str,
        extra_params: dict[str, Any] | None = None,
        output_subdir: str = '',
    ):
        q: queue.Queue[dict[str, Any]] = queue.Queue()

        def _worker() -> None:
            try:
                payload, status = self._generate_result(
                    prompt, model, aspect_ratio, image_size, temperature,
                    temperature_force, system, system_force, provider, api,
                    extra_params, output_subdir,
                )
                q.put({'event': 'done', 'status': status, **payload})
            except Exception as e:
                logger.exception('image generation stream failed')
                q.put({'event': 'done', 'status': 500, 'error': str(e)})

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        while True:
            event = q.get()
            yield json.dumps(event, default=str) + '\n'
            if event.get('event') == 'done':
                break
        t.join(timeout=1.0)

    def _generate(self, request):
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)

        prompt       = (data.get('prompt') or '').strip()
        provider_param = (data.get('provider') or '').strip() or None
        try:
            provider, api = normalize_provider_api(provider_param)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)

        model        = (data.get('model') or default_image_model(provider, api)).strip()
        aspect_ratio = data.get('aspect_ratio', default_aspect_ratio(provider, api))
        image_size   = data.get('image_size', default_image_size(provider, api))
        raw_temperature = data.get('temperature')
        raw_temperature_force = data.get('temperature_force')
        raw_system = data.get('system')
        raw_system_force = data.get('system_force')
        if raw_temperature in (None, ''):
            temperature = None
        else:
            try:
                temperature = float(raw_temperature)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'invalid temperature'}, status=400)
            if temperature < 0.0 or temperature > 2.0:
                return JsonResponse({'error': 'invalid temperature'}, status=400)
        if raw_temperature_force in (None, ''):
            temperature_force = None
        else:
            try:
                temperature_force = float(raw_temperature_force)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'invalid temperature_force'}, status=400)
            if temperature_force < 0.0 or temperature_force > 2.0:
                return JsonResponse({'error': 'invalid temperature_force'}, status=400)
        if temperature is not None and temperature_force is not None:
            return JsonResponse({
                'error': 'temperature and temperature_force are mutually exclusive',
            }, status=400)
        system = raw_system.strip() if isinstance(raw_system, str) else None
        system_force = (
            raw_system_force.strip() if isinstance(raw_system_force, str) else None
        )
        if system == '':
            system = None
        if system_force == '':
            system_force = None
        if system is not None and system_force is not None:
            return JsonResponse({
                'error': 'system and system_force are mutually exclusive',
            }, status=400)

        if not prompt:
            return JsonResponse({'error': 'prompt is required'}, status=400)
        if aspect_ratio not in aspect_ratios(provider, api):
            return JsonResponse({'error': 'invalid aspect_ratio'}, status=400)
        if image_size not in image_sizes(provider, api):
            return JsonResponse({'error': 'invalid image_size'}, status=400)

        try:
            extra_params: dict[str, Any] = extract_extra_params(provider, api, data)
        except LLemonImageParamError as e:
            return JsonResponse({'error': str(e)}, status=400)

        quality_val = data.get('quality')
        if isinstance(quality_val, str) and quality_val.strip():
            extra_params['quality'] = quality_val.strip()

        if not self._media_dir():
            return JsonResponse({'error': 'media_dir not configured'}, status=500)

        output_subdir_raw = str(data.get('output_subdir') or '').strip()
        try:
            output_subdir = self._safe_subdir(output_subdir_raw)
        except ValueError:
            return JsonResponse({'error': 'invalid output_subdir'}, status=400)
        if output_subdir:
            gallery_dir_check = self._gallery_dir()
            if not gallery_dir_check or not self._validated_project_dir(gallery_dir_check, output_subdir):
                return JsonResponse({'error': 'invalid output_subdir'}, status=400)

        if data.get('stream'):
            resp = StreamingHttpResponse(
                self._generate_stream(
                    prompt, model, aspect_ratio, image_size, temperature,
                    temperature_force, system, system_force, provider, api,
                    extra_params or None, output_subdir,
                ),
                content_type='application/x-ndjson',
            )
            resp['Cache-Control'] = 'no-cache'
            resp['X-Accel-Buffering'] = 'no'
            return resp

        payload, status = self._generate_result(
            prompt, model, aspect_ratio, image_size, temperature,
            temperature_force, system, system_force, provider, api,
            extra_params or None, output_subdir,
        )
        return JsonResponse(payload, status=status)

    def _models_json(self, request):
        provider_param = request.GET.get('provider', '').strip() or None
        try:
            provider, api = normalize_provider_api(provider_param)
            raw_models = list_image_models_with_metadata(provider, api)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
            logger.exception('could not list image generation models for provider %s',
                             provider_param)
            return JsonResponse({'error': f'could not list models: {e}'}, status=502)
        model_options = []
        model_descriptions: dict[str, str] = {}
        model_quirks: dict[str, list[str]] = {}
        model_system_prompts: dict[str, str] = {}
        model_qualities: dict[str, dict] = {}
        for m in raw_models:
            mid  = m['id']
            name = m['name']
            model_options.append({
                'id':      mid,
                'display': f'{name} ({mid})' if name else mid,
            })
            model_descriptions[mid] = m['description']
            quirks = model_quirk_labels(mid, provider, api)
            if quirks:
                model_quirks[mid] = quirks
            system_prompt = default_system_prompt(mid, provider, api)
            if system_prompt is not None:
                model_system_prompts[mid] = system_prompt
            try:
                caps = model_capabilities(mid, provider, api)
                quals = caps.get('qualities') or []
                if quals:
                    model_qualities[mid] = {
                        'qualities': quals,
                        'default': caps.get('default_quality'),
                    }
            except Exception:
                pass
        model_tag_states = self._model_tag_states(
            provider, [opt['id'] for opt in model_options],
        )
        return JsonResponse({
            'provider':             provider,
            'api':                  api,
            'model_options':        model_options,
            'model_tag_states':     model_tag_states,
            'model_descriptions':   model_descriptions,
            'model_quirks':         model_quirks,
            'model_system_prompts': model_system_prompts,
            'model_qualities':      model_qualities,
            'aspect_ratios':        aspect_ratios(provider, api),
            'image_sizes':          image_sizes(provider, api),
            'default_model':        default_image_model(provider, api),
            'default_aspect_ratio': default_aspect_ratio(provider, api),
            'default_image_size':   default_image_size(provider, api),
            'provider_config':      _provider_config(provider, api),
            'supports_edit':        supports_edit(provider, api),
            'supports_upscale':     supports_upscale(provider, api),
            'edit_models':          edit_models(provider, api),
            'default_edit_model':   default_edit_model(provider, api),
            'edit_aspect_ratios':   ([''] + edit_aspect_ratios(provider, api)
                                     if edit_aspect_ratios(provider, api) else []),
        })

    def _model_tag_states(self, provider: str, model_ids: list[str]) -> dict[str, dict[str, bool]]:
        try:
            return get_model_tag_states(provider, model_ids)
        except Exception:
            logger.exception('could not load image model tag states')
            return {}

    def _model_note(self, request):
        if request.method == 'GET':
            provider = request.GET.get('provider', '').strip()
            model_id = request.GET.get('model', '').strip()
            if not provider or not model_id:
                return JsonResponse({'error': 'provider and model are required'}, status=400)
            try:
                notes, tags = get_model_note(provider, model_id)
            except Exception as e:
                return JsonResponse({'error': str(e)}, status=500)
            return JsonResponse({'notes': notes, 'tags': tags})

        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)
        provider = (data.get('provider') or '').strip()
        model_id = (data.get('model') or '').strip()
        notes    = data.get('notes', '')
        raw_tags = data.get('tags', {})
        submitted = (
            {k: bool(v) for k, v in raw_tags.items() if isinstance(k, str)}
            if isinstance(raw_tags, dict) else {}
        )
        if not provider or not model_id:
            return JsonResponse({'error': 'provider and model are required'}, status=400)
        try:
            set_model_note(provider, model_id, notes, submitted)
            _notes, tags = get_model_note(provider, model_id)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
        return JsonResponse({'ok': True, 'tags': tags})

    def _do_delete_image(self, request, media_dir: str, thumb_dir: str, large_thumb_dir: str = ''):
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)

        try:
            filename = self._safe_filename(str(data.get('filename') or ''))
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)

        raw_subdir = str(data.get('subdir') or '').strip()
        if raw_subdir:
            try:
                subdir = self._safe_subdir(raw_subdir)
            except ValueError:
                return JsonResponse({'error': 'invalid subdir'}, status=400)
            project_dir = self._validated_project_dir(media_dir, subdir)
            if not project_dir:
                return JsonResponse({'error': 'invalid subdir'}, status=400)
            media_dir = project_dir
            thumb_dir = self._thumb_dir(project_dir)
            large_thumb_dir = self._large_thumb_dir(project_dir)

        if not media_dir:
            return JsonResponse({'error': 'media_dir not configured'}, status=500)

        ext = os.path.splitext(filename)[1].lower()
        try:
            if ext in VIDEO_EXTS:
                delete_video_asset(media_dir, filename, thumb_dir, large_thumb_dir)
            else:
                delete_image_asset(media_dir, filename, thumb_dir, large_thumb_dir)
        except FileNotFoundError:
            return JsonResponse({'error': 'file not found'}, status=404)
        except ValueError:
            return JsonResponse({'error': 'invalid filename'}, status=400)

        return JsonResponse({'deleted': filename})

    def _delete_image(self, request):
        return self._do_delete_image(request, self._gallery_dir(), self._thumb_dir(),
                                     self._large_thumb_dir())

    def _write_upload_sidecars(self, media_dir: str, saved: list[str]) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        for fname in saved:
            payload = {
                'source': 'upload',
                'timestamp': timestamp,
                'uploaded_at': timestamp,
                'files': [fname],
            }
            try:
                write_operation_sidecar(os.path.join(media_dir, fname), payload)
            except OSError as e:
                logger.warning('could not write upload metadata for %s: %s', fname, e)

    def _upload(self, request):
        gallery_dir = self._gallery_dir()
        if not gallery_dir:
            return JsonResponse({'error': 'media_dir not configured'}, status=500)

        subdir_raw = request.POST.get('subdir', '').strip()
        if subdir_raw:
            try:
                subdir = self._safe_subdir(subdir_raw)
            except ValueError:
                return JsonResponse({'error': 'invalid subdir'}, status=400)
            upload_dir = self._validated_project_dir(gallery_dir, subdir)
            if not upload_dir:
                return JsonResponse({'error': 'invalid subdir'}, status=400)
        else:
            upload_dir = gallery_dir

        files = request.FILES.getlist('images')
        if not files:
            return JsonResponse({'error': 'no files uploaded'}, status=400)

        try:
            saved, errors = save_uploaded_image_files(files, upload_dir)
        except OSError as e:
            return JsonResponse({'error': f'could not create gallery directory: {e}'}, status=500)

        self._write_upload_sidecars(upload_dir, saved)

        return JsonResponse({'files': saved, 'errors': errors})

    # ------------------------------------------------------------------ #
    # Venice upscale / edit                                              #
    # ------------------------------------------------------------------ #

    def _read_image_as_data_url(
        self, filename: str, source_dir: 'str | None' = None,
    ) -> 'tuple[str, str | None]':
        """Return (data_url, error_message).  error_message is None on success."""
        dir_ = source_dir if source_dir is not None else self._gallery_dir()
        if not dir_:
            return '', 'media_dir not configured'
        try:
            filename = self._safe_image_name(filename)
        except ValueError as e:
            return '', str(e)
        try:
            return image_as_data_url(dir_, filename), None
        except FileNotFoundError:
            return '', 'file not found'
        except ValueError as e:
            return '', str(e)
        except OSError as e:
            return '', str(e)

    def _save_operation_result(
        self,
        result: dict[str, Any],
        media_dir: str,
        stem: str,
        sidecar: dict[str, Any],
        backend_cls: type,
    ) -> 'tuple[dict[str, Any], int]':
        images = result.get('images', [])
        if not images:
            return {'error': 'no image returned'}, 502

        try:
            files, desc = save_operation_images(
                backend_cls.write_images, images, media_dir, stem,
            )
        except Exception as e:
            logger.exception('could not write operation result images')
            return {'error': f'could not write image: {e}'}, 500

        sidecar = _sanitize_image_metadata(sidecar)
        sidecar['files'] = files
        sidecar['timestamp'] = datetime.now(timezone.utc).isoformat()
        try:
            write_operation_sidecar(desc, sidecar)
        except OSError as e:
            logger.warning('could not write operation metadata: %s', e)

        return {'file': files[0], 'files': files}, 200

    def _upscale_result(
        self,
        data_url: str,
        source_filename: str,
        media_dir: str,
        kwargs: dict[str, Any],
        provider: str,
        api: str,
    ) -> 'tuple[dict[str, Any], int]':
        backend_cls = make_imagegen_backend(provider, api)
        backend = backend_cls(model='upscale', log_dir=self._log_dir())
        try:
            result = backend.upscale(data_url, **kwargs)
        finally:
            backend.shutdown()
        if result.get('error'):
            err = result['error']
            return {'error': err['message']}, 502
        stem = os.path.splitext(source_filename)[0] + '_upscaled'
        sidecar: dict[str, Any] = {
            'operation': 'upscale',
            'source':    source_filename,
            **{k: v for k, v in kwargs.items()},
        }
        return self._save_operation_result(result, media_dir, stem, sidecar, backend_cls)

    def _upscale_stream(self, data_url: str, source_filename: str,
                        media_dir: str, kwargs: dict[str, Any],
                        provider: str, api: str):
        q: queue.Queue[dict[str, Any]] = queue.Queue()

        def _worker() -> None:
            try:
                payload, status = self._upscale_result(data_url, source_filename,
                                                       media_dir, kwargs, provider, api)
                q.put({'event': 'done', 'status': status, **payload})
            except Exception as e:
                logger.exception('upscale stream failed')
                q.put({'event': 'done', 'status': 500, 'error': str(e)})

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        while True:
            event = q.get()
            yield json.dumps(event, default=str) + '\n'
            if event.get('event') == 'done':
                break
        t.join(timeout=1.0)

    def _do_upscale(self, request, source_dir: str, result_dir: 'str | None' = None):
        result_dir = result_dir if result_dir is not None else source_dir
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)

        try:
            provider, api = normalize_provider_api((data.get('provider') or '').strip() or None)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        if not supports_upscale(provider, api):
            return JsonResponse({'error': f'upscale not supported by provider {provider!r}'},
                                status=400)

        filename = str(data.get('filename') or '')
        data_url, err = self._read_image_as_data_url(filename, source_dir=source_dir)
        if err:
            return JsonResponse({'error': err}, status=400 if err != 'media_dir not configured' else 500)
        if not result_dir:
            return JsonResponse({'error': 'media_dir not configured'}, status=500)

        kwargs: dict[str, Any] = {}
        scale = data.get('scale')
        if scale is not None:
            try:
                kwargs['scale'] = int(scale)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'invalid scale'}, status=400)
        enhance = data.get('enhance')
        if enhance is not None:
            kwargs['enhance'] = bool(enhance)
        ep = data.get('enhance_prompt')
        if isinstance(ep, str) and ep.strip():
            kwargs['enhance_prompt'] = ep.strip()
        ec = data.get('enhance_creativity')
        if ec is not None:
            try:
                kwargs['enhance_creativity'] = float(ec)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'invalid enhance_creativity'}, status=400)
        rep = data.get('replication')
        if rep is not None:
            try:
                kwargs['replication'] = float(rep)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'invalid replication'}, status=400)

        if data.get('stream'):
            resp = StreamingHttpResponse(
                self._upscale_stream(data_url, filename, result_dir, kwargs, provider, api),
                content_type='application/x-ndjson',
            )
            resp['Cache-Control'] = 'no-cache'
            resp['X-Accel-Buffering'] = 'no'
            return resp

        payload, status = self._upscale_result(data_url, filename, result_dir, kwargs,
                                               provider, api)
        return JsonResponse(payload, status=status)

    def _upscale(self, request):
        return self._do_upscale(request, self._gallery_dir())

    def _upscale_archive(self, request):
        return self._do_upscale(request, self._archive_dir(), self._gallery_dir())

    def _edit_result(
        self,
        data_url: str,
        source_filename: str,
        media_dir: str,
        prompt: str,
        edit_model: str,
        aspect_ratio: str | None,
        safe_mode: bool | None,
        provider: str,
        api: str,
    ) -> 'tuple[dict[str, Any], int]':
        backend_cls = make_imagegen_backend(provider, api)
        backend = backend_cls(model=edit_model, log_dir=self._log_dir())
        try:
            result = backend.edit(
                data_url, prompt, model=edit_model,
                aspect_ratio=aspect_ratio, safe_mode=safe_mode,
            )
        finally:
            backend.shutdown()
        if result.get('error'):
            err = result['error']
            return {'error': err['message']}, 502
        stem = os.path.splitext(source_filename)[0] + '_edit'
        sidecar: dict[str, Any] = {
            'operation':    'edit',
            'source':       source_filename,
            'prompt':       prompt,
            'model':        edit_model,
        }
        if aspect_ratio:
            sidecar['aspect_ratio'] = aspect_ratio
        return self._save_operation_result(result, media_dir, stem, sidecar, backend_cls)

    def _edit_stream(self, data_url: str, source_filename: str, media_dir: str,
                     prompt: str, edit_model: str, aspect_ratio: 'str | None',
                     safe_mode: 'bool | None', provider: str, api: str):
        q: queue.Queue[dict[str, Any]] = queue.Queue()

        def _worker() -> None:
            try:
                payload, status = self._edit_result(data_url, source_filename, media_dir,
                                                    prompt, edit_model, aspect_ratio,
                                                    safe_mode, provider, api)
                q.put({'event': 'done', 'status': status, **payload})
            except Exception as e:
                logger.exception('edit stream failed')
                q.put({'event': 'done', 'status': 500, 'error': str(e)})

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        while True:
            event = q.get()
            yield json.dumps(event, default=str) + '\n'
            if event.get('event') == 'done':
                break
        t.join(timeout=1.0)

    def _do_edit_image(self, request, source_dir: str, result_dir: 'str | None' = None):
        result_dir = result_dir if result_dir is not None else source_dir
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)

        try:
            provider, api = normalize_provider_api((data.get('provider') or '').strip() or None)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        if not supports_edit(provider, api):
            return JsonResponse({'error': f'edit not supported by provider {provider!r}'},
                                status=400)

        filename = str(data.get('filename') or '')
        data_url, err = self._read_image_as_data_url(filename, source_dir=source_dir)
        if err:
            return JsonResponse({'error': err}, status=400 if err != 'media_dir not configured' else 500)

        prompt = (data.get('prompt') or '').strip()
        if not prompt:
            return JsonResponse({'error': 'prompt is required'}, status=400)

        edit_model   = (data.get('model') or default_edit_model(provider, api)).strip()
        valid_ratios = edit_aspect_ratios(provider, api)
        aspect_ratio = (data.get('aspect_ratio') or '').strip() or None
        if aspect_ratio and aspect_ratio not in valid_ratios:
            return JsonResponse({'error': 'invalid aspect_ratio'}, status=400)
        safe_mode_raw = data.get('safe_mode')
        safe_mode: bool | None = bool(safe_mode_raw) if safe_mode_raw is not None else None

        if not result_dir:
            return JsonResponse({'error': 'media_dir not configured'}, status=500)

        if data.get('stream'):
            resp = StreamingHttpResponse(
                self._edit_stream(data_url, filename, result_dir, prompt,
                                  edit_model, aspect_ratio, safe_mode, provider, api),
                content_type='application/x-ndjson',
            )
            resp['Cache-Control'] = 'no-cache'
            resp['X-Accel-Buffering'] = 'no'
            return resp

        payload, status = self._edit_result(data_url, filename, result_dir,
                                            prompt, edit_model, aspect_ratio, safe_mode,
                                            provider, api)
        return JsonResponse(payload, status=status)

    def _edit_image(self, request):
        return self._do_edit_image(request, self._gallery_dir())

    def _edit_archive_image(self, request):
        return self._do_edit_image(request, self._archive_dir(), self._gallery_dir())

    def image_file(self, request, filename):
        try:
            filename = self._safe_filename(filename)
        except ValueError:
            raise Http404
        path = os.path.join(self._gallery_dir(), filename)
        if not os.path.isfile(path):
            raise Http404
        mime, _ = mimetypes.guess_type(filename)
        return FileResponse(open(path, 'rb'),
                            content_type=mime or 'application/octet-stream')

    def thumbnail(self, request, filename):
        if '/' in filename or filename.startswith('.'):
            raise Http404
        if not self._ensure_thumbnail(filename):
            raise Http404
        # For videos, the thumbnail has a different name
        thumb_filename = filename if not is_video(filename) else video_thumb_name(filename)
        path = os.path.join(self._thumb_dir(), thumb_filename)
        if not os.path.isfile(path):
            raise Http404
        mime, _ = mimetypes.guess_type(path)
        return FileResponse(open(path, 'rb'),
                            content_type=mime or 'application/octet-stream')

    def _archive_image_file(self, request, filename):
        try:
            filename = self._safe_filename(filename)
        except ValueError:
            raise Http404
        path = os.path.join(self._archive_dir(), filename)
        if not os.path.isfile(path):
            raise Http404
        mime, _ = mimetypes.guess_type(filename)
        return FileResponse(open(path, 'rb'),
                            content_type=mime or 'application/octet-stream')

    def _archive_thumbnail(self, request, filename):
        if '/' in filename or filename.startswith('.'):
            raise Http404
        if not self._ensure_archive_thumbnail(filename):
            raise Http404
        thumb_filename = filename if not is_video(filename) else video_thumb_name(filename)
        path = os.path.join(self._archive_thumb_dir(), thumb_filename)
        if not os.path.isfile(path):
            raise Http404
        mime, _ = mimetypes.guess_type(path)
        return FileResponse(open(path, 'rb'),
                            content_type=mime or 'application/octet-stream')

    def _large_thumbnail(self, request, filename):
        if '/' in filename or filename.startswith('.'):
            raise Http404
        if not self._ensure_large_thumbnail(filename):
            raise Http404
        # For videos, the thumbnail has a different name
        thumb_filename = filename if not is_video(filename) else video_thumb_name(filename)
        path = os.path.join(self._large_thumb_dir(), thumb_filename)
        if not os.path.isfile(path):
            raise Http404
        mime, _ = mimetypes.guess_type(path)
        return FileResponse(open(path, 'rb'),
                            content_type=mime or 'application/octet-stream')

    def _archive_large_thumbnail(self, request, filename):
        if '/' in filename or filename.startswith('.'):
            raise Http404
        if not self._ensure_archive_large_thumbnail(filename):
            raise Http404
        thumb_filename = filename if not is_video(filename) else video_thumb_name(filename)
        path = os.path.join(self._archive_large_thumb_dir(), thumb_filename)
        if not os.path.isfile(path):
            raise Http404
        mime, _ = mimetypes.guess_type(path)
        return FileResponse(open(path, 'rb'),
                            content_type=mime or 'application/octet-stream')

    def _delete_archive_image(self, request):
        return self._do_delete_image(request, self._archive_dir(), self._archive_thumb_dir(),
                                     self._archive_large_thumb_dir())

    def _do_move_image(
        self, request,
        src_dir: str, dst_dir: str,
        src_thumb_dir: str, dst_thumb_dir: str,
        src_large_thumb_dir: str = '', dst_large_thumb_dir: str = '',
        allow_from_subdir: bool = False,
        data: dict | None = None,
    ):
        if data is None:
            try:
                data = json.loads(request.body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)

        try:
            filename = self._safe_filename(str(data.get('filename') or ''))
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)

        if allow_from_subdir:
            raw_subdir = str(data.get('subdir') or '').strip()
            if raw_subdir:
                try:
                    subdir = self._safe_subdir(raw_subdir)
                except ValueError:
                    return JsonResponse({'error': 'invalid subdir'}, status=400)
                project_dir = self._validated_project_dir(src_dir, subdir)
                if not project_dir:
                    return JsonResponse({'error': 'invalid subdir'}, status=400)
                src_dir = project_dir
                src_thumb_dir = self._thumb_dir(project_dir)
                src_large_thumb_dir = self._large_thumb_dir(project_dir)

        if not src_dir or not dst_dir:
            return JsonResponse({'error': 'media_dir not configured'}, status=500)

        ext = os.path.splitext(filename)[1].lower()
        try:
            if ext in VIDEO_EXTS:
                dst_fname = move_video_asset(
                    src_dir, dst_dir, filename,
                    src_thumb_dir, dst_thumb_dir,
                    src_large_thumb_dir, dst_large_thumb_dir,
                )
            else:
                dst_fname = move_image_asset(
                    src_dir, dst_dir, filename,
                    src_thumb_dir, dst_thumb_dir,
                    src_large_thumb_dir, dst_large_thumb_dir,
                )
        except FileNotFoundError:
            return JsonResponse({'error': 'file not found'}, status=404)
        except ValueError:
            return JsonResponse({'error': 'invalid filename'}, status=400)
        except OSError as e:
            return JsonResponse({'error': str(e)}, status=500)

        return JsonResponse({'moved': dst_fname})

    def _move_to_archive(self, request):
        try:
            data = json.loads(request.body)
            filename = self._safe_filename(str(data.get('filename') or ''))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        dst_dir = self._archive_dir_for_filename(filename)
        return self._do_move_image(
            request,
            src_dir=self._gallery_dir(), dst_dir=dst_dir,
            src_thumb_dir=self._thumb_dir(), dst_thumb_dir=self._archive_thumb_dir(dst_dir),
            src_large_thumb_dir=self._large_thumb_dir(),
            dst_large_thumb_dir=self._archive_large_thumb_dir(dst_dir),
            allow_from_subdir=True,
            data=data,
        )

    def _move_to_gallery(self, request):
        return self._do_move_image(
            request,
            src_dir=self._archive_dir(), dst_dir=self._gallery_dir(),
            src_thumb_dir=self._archive_thumb_dir(), dst_thumb_dir=self._thumb_dir(),
            src_large_thumb_dir=self._archive_large_thumb_dir(),
            dst_large_thumb_dir=self._large_thumb_dir(),
        )

    def _gallery_project_file(self, request, subpath: str):
        gallery_dir = self._gallery_dir()
        if not gallery_dir or '/' not in subpath:
            raise Http404
        subdir, fname = subpath.rsplit('/', 1)
        try:
            subdir = self._safe_subdir(subdir)
            fname = self._safe_filename(fname)
        except ValueError:
            raise Http404
        project_dir = self._validated_project_dir(gallery_dir, subdir)
        if not project_dir:
            raise Http404
        file_path = os.path.join(project_dir, fname)
        if not os.path.isfile(file_path):
            raise Http404
        mime, _ = mimetypes.guess_type(file_path)
        return FileResponse(open(file_path, 'rb'), content_type=mime or 'application/octet-stream')

    def _gallery_project_thumb(self, request, subpath: str):
        gallery_dir = self._gallery_dir()
        if not gallery_dir or '/' not in subpath:
            raise Http404
        subdir, fname = subpath.rsplit('/', 1)
        try:
            subdir = self._safe_subdir(subdir)
            fname = self._safe_filename(fname)
        except ValueError:
            raise Http404
        project_dir = self._validated_project_dir(gallery_dir, subdir)
        if not project_dir:
            raise Http404
        thumb_dir = self._thumb_dir(project_dir)
        if not ensure_media_thumbnail(project_dir, thumb_dir, fname, 160):
            raise Http404
        thumb_fname = fname if not is_video(fname) else video_thumb_name(fname)
        path = os.path.join(thumb_dir, thumb_fname)
        if not os.path.isfile(path):
            raise Http404
        mime, _ = mimetypes.guess_type(path)
        return FileResponse(open(path, 'rb'), content_type=mime or 'image/jpeg')

    def _gallery_project_large_thumb(self, request, subpath: str):
        gallery_dir = self._gallery_dir()
        if not gallery_dir or '/' not in subpath:
            raise Http404
        subdir, fname = subpath.rsplit('/', 1)
        try:
            subdir = self._safe_subdir(subdir)
            fname = self._safe_filename(fname)
        except ValueError:
            raise Http404
        project_dir = self._validated_project_dir(gallery_dir, subdir)
        if not project_dir:
            raise Http404
        large_thumb_dir = self._large_thumb_dir(project_dir)
        if not ensure_media_thumbnail(project_dir, large_thumb_dir, fname, 600):
            raise Http404
        thumb_fname = fname if not is_video(fname) else video_thumb_name(fname)
        path = os.path.join(large_thumb_dir, thumb_fname)
        if not os.path.isfile(path):
            raise Http404
        mime, _ = mimetypes.guess_type(path)
        return FileResponse(open(path, 'rb'), content_type=mime or 'image/jpeg')

    def _gallery_create_project(self, request):
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)
        gallery_dir = self._gallery_dir()
        if not gallery_dir:
            return JsonResponse({'error': 'gallery not configured'}, status=500)
        name = str(data.get('name') or '').strip()
        if not name or '/' in name or '\\' in name or name.startswith('.') or name in _RESERVED_GALLERY_DIRS:
            return JsonResponse({'error': 'invalid project name'}, status=400)
        raw_subdir = str(data.get('subdir') or '').strip()
        if raw_subdir:
            try:
                subdir = self._safe_subdir(raw_subdir)
            except ValueError:
                return JsonResponse({'error': 'invalid subdir'}, status=400)
            parent_dir = self._validated_project_dir(gallery_dir, subdir)
            if not parent_dir:
                return JsonResponse({'error': 'invalid subdir'}, status=400)
        else:
            parent_dir = gallery_dir
        new_dir = os.path.join(parent_dir, name)
        try:
            os.makedirs(new_dir, exist_ok=False)
        except FileExistsError:
            return JsonResponse({'error': 'project already exists'}, status=409)
        except OSError as e:
            return JsonResponse({'error': str(e)}, status=500)
        return JsonResponse({'created': name})

    def _gallery_project_move(self, request):
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)
        gallery_dir = self._gallery_dir()
        if not gallery_dir:
            return JsonResponse({'error': 'gallery not configured'}, status=500)
        try:
            filename = self._safe_filename(str(data.get('filename') or ''))
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        try:
            from_subdir = self._safe_subdir(str(data.get('from_subdir') or ''))
            to_subdir = self._safe_subdir(str(data.get('to_subdir') or ''))
        except ValueError:
            return JsonResponse({'error': 'invalid subdir'}, status=400)
        if from_subdir == to_subdir:
            return JsonResponse({'error': 'source and destination are the same'}, status=400)
        src_dir = self._validated_project_dir(gallery_dir, from_subdir) if from_subdir else gallery_dir
        if not src_dir:
            return JsonResponse({'error': 'invalid from_subdir'}, status=400)
        dst_dir = self._validated_project_dir(gallery_dir, to_subdir) if to_subdir else gallery_dir
        if not dst_dir:
            return JsonResponse({'error': 'invalid to_subdir'}, status=400)
        ext = os.path.splitext(filename)[1].lower()
        try:
            if ext in VIDEO_EXTS:
                dst_fname = move_video_asset(
                    src_dir, dst_dir, filename,
                    self._thumb_dir(src_dir), self._thumb_dir(dst_dir),
                    self._large_thumb_dir(src_dir), self._large_thumb_dir(dst_dir),
                )
            else:
                dst_fname = move_image_asset(
                    src_dir, dst_dir, filename,
                    self._thumb_dir(src_dir), self._thumb_dir(dst_dir),
                    self._large_thumb_dir(src_dir), self._large_thumb_dir(dst_dir),
                )
        except FileNotFoundError:
            return JsonResponse({'error': 'file not found'}, status=404)
        except (ValueError, FileExistsError) as e:
            return JsonResponse({'error': str(e)}, status=400)
        except OSError as e:
            return JsonResponse({'error': str(e)}, status=500)
        return JsonResponse({'moved': dst_fname})

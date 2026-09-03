"""llemon_djview.imagegen - Django view logic for LLemon image generation.

Each app instantiates LLemonImageGenViewSet with its own template prefix and URL namespace.
"""

import json
import logging
import mimetypes
import os
import queue
import re
import threading
from copy import deepcopy
from dataclasses import asdict
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
    default_model_for_presentation,
    default_image_size,
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
    list_edit_models_with_metadata,
    list_image_models_with_metadata,
    make_imagegen_backend,
    model_display,
    model_presentation,
    model_scoped_parameters,
    normalize_edit_inputs,
    normalize_provider_api,
    preflight_request,
    provider_config as _provider_config,
    resolve_action_model,
    supports_edit,
    supports_upscale,
    unsupported_extra_params,
    PROVIDERS,
    write_image_generation_exif_with_sidecar_fallback,
    write_image_metadata,
    LLemonImageEditInputError,
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
from .media_creator import (
    build_creator_presentation,
    build_model_target,
    build_operation_presentation,
)

logger = logging.getLogger(__name__)


_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
_MEDIA_EXTS = _IMAGE_EXTS | VIDEO_EXTS


def _sanitize_image_metadata(value: Any) -> Any:
    return sanitize_metadata_data_urls(value)


def _notice_dict(notice: Any) -> dict[str, Any]:
    """Return one complete JSON-safe model-information notice."""
    result = asdict(notice)
    result['models'] = list(result['models'])
    return result


def _source_kind_usability(
    scope: dict[str, Any], source_kind: str,
) -> 'tuple[str, str | None]':
    """Classify one source kind against one scope's local facts.

    ``scope`` is either an ``edit_inputs``/``edit_input`` record (top-level,
    for an ordered schema) or one role dict from ``edit_inputs['roles']``
    (for a named schema) -- both carry the same three fields at their own
    scope. Returns ``('usable' | 'unavailable_transport' | 'unsupported',
    transport_or_None)``. Mirrors normalize_edit_inputs()'s and
    edit_images_availability()'s identical precedence
    (specs/mediagen-image-spec.md, "Caller source kinds and backend
    transports"): a kind carrying a required transport is deliverable
    whenever that transport is available, and need not also appear in
    accepted_source_kinds -- which lists kinds submittable directly,
    unchanged, with no transform. Segmind's array/named-role shapes declare
    the two facts disjointly (e.g. accepted_source_kinds=['https_url']
    alongside required_backend_transports={'data_url': 'provider_upload'}),
    so checking acceptance first would make the transport fact unreachable
    and silently defeat every upload-backed model -- the same precedence
    bug Task 8 Phase 5 fixed in normalize_edit_inputs() itself.
    """
    required = scope['required_backend_transports'].get(source_kind)
    if required is not None:
        if required in scope['available_backend_transports']:
            return 'usable', required
        return 'unavailable_transport', required
    if source_kind in scope['accepted_source_kinds']:
        return 'usable', None
    return 'unsupported', None


def _operation_state(
    row: dict[str, Any], operation: str, *, source_kind: str | None = None,
) -> tuple[bool, bool, str | None]:
    """Return (eligible, enabled, brief reason) from normalized presentation.

    ``operation='edit_images'`` reads the provider-neutral multi-image
    facade (``edit_inputs``/``operations.edit_images``) rather than the
    single-image ``edit_input``/``operations.edit`` — the two agree for
    every model at today's effective maximum of one image (see
    specs/mediagen-image-spec.md, "Agreement with edit_input is scoped, not
    universal"), so this is not a behavior change for any model Grove could
    already select. A named schema is fully selectable here (Task 13 Phase
    2 added the role-assignment UI): its source-kind check is evaluated
    per role rather than against the top-level accepted_source_kinds/
    required_backend_transports, which are only the cross-role
    intersection (specs/mediagen-image-spec.md, "Capability schema") and
    can be empty even when every individual role independently accepts
    ``source_kind`` through its own (possibly different) transport --
    using the flattened intersection here would wrongly disable a model
    ``operations.edit_images.available`` already reports as usable.
    """
    presentation = row['presentation']
    detail = presentation['detail']
    operation_data = presentation['operations'][operation]
    if detail == 'summary':
        return True, False, 'model detail has not been resolved'
    if not operation_data['available']:
        return False, False, operation_data['unavailable_reason']
    if source_kind is None:
        return True, True, None
    inputs_key = 'edit_inputs' if operation == 'edit_images' else 'edit_input'
    edit_input = presentation[inputs_key]
    roles = edit_input.get('roles') if operation == 'edit_images' else None
    if roles:
        # Mirrors edit_images_availability()'s candidate selection: every
        # required role must be usable, or -- when none are required --
        # at least one optional role must be, since an empty input list is
        # never itself a valid request.
        required_roles = [role for role in roles if role['required']]
        candidates = required_roles or roles
        results = [_source_kind_usability(role, source_kind) for role in candidates]
        states = [state for state, _ in results]
        usable = all(s == 'usable' for s in states) if required_roles \
            else any(s == 'usable' for s in states)
        if not usable:
            reason = ('required transport unavailable'
                      if 'unavailable_transport' in states else 'data URL unsupported')
            return False, False, reason
        # Same required-vs-optional split as the usability check above: with
        # required roles (AND semantics), one warned usable role forces
        # consent for the whole call, so *any* warned usable candidate
        # disables it. With only optional roles (OR semantics), a warned
        # usable role does not disable the model as long as some other
        # usable role needs no consent at all -- only when *every* usable
        # candidate requires a warned transport is there no warning-free
        # request left to offer.
        def _is_warned(transport: str | None) -> bool:
            return bool(transport) and transport in edit_input['transport_warnings']
        usable_transports = [t for state, t in results if state == 'usable']
        warned = (
            any(_is_warned(t) for t in usable_transports) if required_roles
            else all(_is_warned(t) for t in usable_transports)
        )
        if warned:
            return False, False, 'requires accepting a data-handling warning'
        return True, True, None
    state, transport = _source_kind_usability(edit_input, source_kind)
    if state == 'unavailable_transport':
        return False, False, 'required transport unavailable'
    if state == 'unsupported':
        return False, False, 'data URL unsupported'
    # Grove does not yet collect data-handling-warning consent (Task 13
    # Phase 2): accept_data_handling_warnings is always False in
    # _edit_result(), so a warned transport can never actually be
    # dispatched through Grove today even though it is "available". Report
    # that accurately here instead of claiming the model is enabled and
    # only failing at dispatch with warning_not_accepted.
    if transport and transport in edit_input['transport_warnings']:
        return False, False, 'requires accepting a data-handling warning'
    return True, True, None


def _edit_input_transport_pending(
    edit_inputs: dict[str, Any], image: dict[str, Any],
) -> bool:
    """True when this canonical edit image's source will be replaced by an
    uploaded asset URL before the request is built (e.g. Segmind's
    provider-upload path), so provider-schema preflight must not validate
    its current raw shape against the wire request it will never actually
    carry. Mirrors llemon-image's identical check and the preflight-
    precedence fix from Task 8 Phase 5 (upgrade.md).
    """
    role_name = image.get('role')
    scope = edit_inputs
    if role_name:
        scope = next(
            (r for r in edit_inputs['roles'] if r['name'] == role_name),
            edit_inputs,
        )
    kind = 'data_url' if image['source'].startswith('data:') else 'https_url'
    return kind in scope['required_backend_transports']


def _model_options(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy complete facade rows and add Grove's display label."""
    options = deepcopy(rows)
    for option in options:
        model_id = option['id']
        name = option.get('name')
        option['display'] = f'{name} ({model_id})' if name else model_id
    return options


def _select_model(
    rows: list[dict[str, Any]], operation: str, requested: str | None,
    default: str | None, *, source_kind: str | None = None,
) -> str | None:
    """Select a row for presentation without manufacturing a default."""
    eligible = {
        row['id'] for row in rows
        if _operation_state(row, operation, source_kind=source_kind)[0]
    }
    requested_id = requested.strip() if isinstance(requested, str) else ''
    for candidate in (requested_id or None, default):
        if candidate in eligible:
            return candidate
    return next((row['id'] for row in rows if row['id'] in eligible), None)


def _discovered_edit_models(
    provider: str, api: str, *, on_model_info_notice=None,
) -> list[dict[str, Any]]:
    """Return complete live-discovered edit model rows.

    Every provider's edit-model discovery is live, cached briefly by LLemon's
    own ``list_edit_models_with_metadata()`` facade so page renders and edit
    requests do not each pay a catalog fetch. A discovery failure or an
    empty result from a provider that declares edit support is a provider
    fault: it raises rather than degrading to a warning, and the caller is
    responsible for turning that into an HTTP error response.
    """
    return list_edit_models_with_metadata(
        provider, api, on_model_info_notice=on_model_info_notice,
    )


def _edit_metadata(
    provider: str, api: str, *, requested_model: str | None = None,
    on_model_info_notice=None,
) -> dict[str, Any]:
    """Edit-control metadata for one provider.

    A provider that does not declare ``supports_edit`` is an ordinary
    supported state and returns cleanly with no request. Otherwise this
    contacts (or reuses LLemon's cache of) live edit-model discovery, which
    raises on failure or an empty result — the caller must let that
    exception become an HTTP error response rather than catching it here.

    Aspect-ratio policy: a provider whose edit ratios include ``auto``
    (Venice) preserves the source ratio through that value; a provider
    without ``auto`` (OpenRouter) resizes to a fixed ratio, so no
    source-preserving choice is offered and a concrete ratio is required.
    Sizing: ``edit_image_sizes`` is empty for Venice single-image edits
    (output size comes from the source image) and non-empty for OpenRouter,
    whose edit path accepts an explicit size.
    """
    if not supports_edit(provider, api):
        return {
            'supports_edit':             False,
            'edit_models':               [],
            'edit_model_options':        [],
            'selected_edit_model':       None,
            'default_edit_model':        '',
            'edit_aspect_ratios':        [],
            'default_edit_aspect_ratio': '',
            'edit_image_sizes':          [],
            'default_edit_image_size':   '',
            'selected_edit_controls':    {
                'availability': {'enabled': False, 'reason': None},
                'aspect_ratios': [], 'default_aspect_ratio': None,
                'image_sizes': [], 'default_image_size': None,
            },
        }
    rows = _discovered_edit_models(
        provider, api, on_model_info_notice=on_model_info_notice,
    )
    default_model = default_model_for_presentation(
        provider, api, operation='edit',
    )
    selected_model = _select_model(
        rows, 'edit_images', requested_model, default_model, source_kind='data_url',
    )
    selected_row = next(
        (row for row in rows if row['id'] == selected_model), None,
    )
    selected_controls = (
        _edit_row_controls(provider, api, selected_row) if selected_row else {
            'availability': {'enabled': False, 'reason': 'no compatible edit model'},
            'aspect_ratios': [], 'default_aspect_ratio': None,
            'image_sizes': [], 'default_image_size': None,
        }
    )
    return {
        'supports_edit':             True,
        'edit_models':               [row['id'] for row in rows],
        'edit_model_options':        _model_options(rows),
        'selected_edit_model':       selected_model,
        'default_edit_model':        default_model or '',
        'edit_aspect_ratios':        selected_controls['aspect_ratios'],
        'default_edit_aspect_ratio': selected_controls['default_aspect_ratio'] or '',
        'edit_image_sizes':          selected_controls['image_sizes'],
        'default_edit_image_size':   selected_controls['default_image_size'] or '',
        'selected_edit_controls':    selected_controls,
    }


def _edit_row_controls(
    provider: str, api: str, row: dict[str, Any],
) -> dict[str, Any]:
    """Derive all edit controls and Grove display defaults from one row."""
    controls = row['presentation']['controls']['edit']
    ratios = list(controls['aspect_ratios'])
    ratio = controls['default_aspect_ratio']
    if ratio is None:
        fallback = default_aspect_ratio(provider, api)
        ratio = ('auto' if 'auto' in ratios else
                 fallback if fallback in ratios else
                 ratios[0] if ratios else None)
    sizes = list(controls['image_sizes'])
    size = controls['default_image_size']
    if size is None:
        fallback_size = default_image_size(provider, api)
        size = (fallback_size if fallback_size in sizes else
                sizes[0] if sizes else None)
    _, enabled, reason = _operation_state(row, 'edit_images', source_kind='data_url')
    return {
        'availability': {'enabled': enabled, 'reason': reason},
        'aspect_ratios': ratios,
        'default_aspect_ratio': ratio,
        'image_sizes': sizes,
        'default_image_size': size,
    }


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
        notices: list[Any] = []
        try:
            provider, api = normalize_provider_api(provider_param)
            raw_models = list_image_models_with_metadata(
                provider, api, on_model_info_notice=notices.append,
            )
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
            logger.exception('could not list image generation models')
            return JsonResponse({'error': f'could not list image generation models: {e}'},
                                status=502)
        try:
            edit_data = _edit_metadata(
                provider, api,
                requested_model=request.GET.get('selected_edit_model'),
                on_model_info_notice=notices.append,
            )
        except Exception as e:
            logger.exception('could not list edit models')
            return JsonResponse({'error': f'could not list edit models: {e}'},
                                status=502)
        requested_model = (
            request.GET.get('model') if 'model' in request.GET else None
        )
        try:
            creator_data = self._creator_data(
                provider, api, raw_models, edit_data,
                requested_model=requested_model,
                on_model_info_notice=notices.append,
            )
        except Exception as e:
            logger.exception('could not resolve image model presentation')
            return JsonResponse(
                {'error': f'could not resolve model presentation: {e}'},
                status=502,
            )
        creator_data['notices'] = [_notice_dict(notice) for notice in notices]

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
                **creator_data,
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
            },
        ))

    def _creator_data(
        self,
        provider: str,
        api: str,
        raw_models: list[dict[str, Any]],
        edit_data: dict[str, Any],
        *,
        requested_model: str | None = None,
        on_model_info_notice=None,
    ) -> dict[str, Any]:
        """Return the provider data shared by initial render and JSON refresh.

        ``edit_data`` is computed by the caller (``_edit_metadata()``) so
        that caller can isolate and report an edit-model discovery failure
        distinctly from any other failure in this method.
        """
        model_options = _model_options(raw_models)
        model_descriptions: dict[str, str] = {}
        for model in model_options:
            model_descriptions[model['id']] = model.get('description') or ''

        model_tag_states = self._model_tag_states(
            provider, [option['id'] for option in model_options],
        )
        ratios = aspect_ratios(provider, api)
        sizes = image_sizes(provider, api)
        default_model = default_model_for_presentation(provider, api)
        default_ratio = default_aspect_ratio(provider, api)
        default_size = default_image_size(provider, api)
        provider_fields = _provider_config(provider, api)
        provider_supports_edit = supports_edit(provider, api)
        provider_supports_upscale = supports_upscale(provider, api)
        selected_model = _select_model(
            model_options, 'generate', requested_model, default_model,
        )
        selected_presentation = None
        if selected_model:
            selected_row = next(
                row for row in model_options if row['id'] == selected_model
            )
            selected_presentation = selected_row['presentation']
            if selected_presentation['detail'] == 'summary':
                selected_presentation = model_presentation(
                    selected_model, provider, api,
                    on_model_info_notice=on_model_info_notice,
                )
                selected_row['presentation'] = selected_presentation
        selected_target = (
            self._image_model_target(
                provider, api, selected_model,
                presentation=selected_presentation,
                on_model_info_notice=on_model_info_notice,
            ) if selected_model and selected_presentation else None
        )
        model_qualities: dict[str, dict[str, Any]] = {}
        if selected_target and selected_model:
            target_controls = selected_target['controls']
            if target_controls.get('qualities'):
                model_qualities[selected_model] = {
                    'qualities': target_controls['qualities'],
                    'default': target_controls.get('default_quality'),
                }

        data: dict[str, Any] = {
            'provider': provider,
            'api': api,
            'model_options': model_options,
            'model_tag_states': model_tag_states,
            'model_descriptions': model_descriptions,
            'model_qualities': model_qualities,
            'aspect_ratios': ratios,
            'image_sizes': sizes,
            'default_model': default_model,
            'selected_model': selected_model,
            'default_aspect_ratio': default_ratio,
            'default_image_size': default_size,
            'provider_config': provider_fields,
            'supports_edit': provider_supports_edit,
            'supports_upscale': provider_supports_upscale,
            **edit_data,
        }
        generation_enabled = bool(
            selected_presentation
            and selected_presentation['operations']['generate']['available']
        )
        generation_reason = (
            None if generation_enabled or not selected_presentation else
            selected_presentation['operations']['generate']['unavailable_reason']
        )
        selected_edit_model = edit_data['selected_edit_model']
        selected_edit_row = next(
            (row for row in edit_data['edit_model_options']
             if row['id'] == selected_edit_model), None,
        )
        # Production _edit_metadata() always supplies this key.  Derivation is
        # retained only for callers/tests that inject the documented flat
        # compatibility aliases instead of invoking that helper.
        selected_edit_controls = edit_data.get('selected_edit_controls')
        if selected_edit_controls is None and selected_edit_row is not None:
            selected_edit_controls = _edit_row_controls(
                provider, api, selected_edit_row,
            )
        edit_enabled = False
        edit_reason = (
            None if not provider_supports_edit else 'no compatible edit model'
        )
        if selected_edit_row:
            _, edit_enabled, edit_reason = _operation_state(
                selected_edit_row, 'edit_images', source_kind='data_url',
            )
        data['presentation'] = build_creator_presentation(provider, api, {
            'generate': build_operation_presentation(
                'generate',
                model_options=model_options,
                selected_model=selected_model,
                default_model=default_model,
                defaults={
                    'aspect_ratio': default_ratio,
                    'image_size': default_size,
                },
                controls={
                    'aspect_ratios': ratios,
                    'image_sizes': sizes,
                    'provider_config': provider_fields,
                },
                availability={
                    'enabled': generation_enabled,
                    'reason': generation_reason,
                },
                selected_target=selected_target,
                notes={
                    'provider': provider,
                    'model_tag_states': model_tag_states,
                },
            ),
            'edit': build_operation_presentation(
                'edit',
                model_options=edit_data['edit_model_options'],
                selected_model=selected_edit_model,
                default_model=edit_data['default_edit_model'] or None,
                defaults={
                    'aspect_ratio': edit_data['default_edit_aspect_ratio'],
                    'image_size': edit_data['default_edit_image_size'],
                },
                controls={
                    'aspect_ratios': edit_data['edit_aspect_ratios'],
                    'image_sizes': edit_data['edit_image_sizes'],
                },
                availability={
                    'enabled': edit_enabled,
                    'reason': edit_reason,
                    'operation_supported': provider_supports_edit,
                },
                selected_target=(
                    build_model_target(
                        provider, api, 'edit', selected_edit_model,
                        controls=selected_edit_controls,
                    )
                    if selected_edit_model else None
                ),
            ),
            'upscale': build_operation_presentation(
                'upscale',
                availability={'enabled': provider_supports_upscale},
            ),
        })
        return data

    @staticmethod
    def _image_model_target(
        provider: str,
        api: str,
        model: str,
        *,
        presentation: dict[str, Any] | None = None,
        on_model_info_notice=None,
    ) -> dict[str, Any]:
        """Return selected-model controls without repeating model discovery."""
        if presentation is None:
            try:
                presentation = model_presentation(
                    model, provider, api,
                    on_model_info_notice=on_model_info_notice,
                )
            except Exception:
                # Preserve the historical best-effort model-switch endpoint:
                # lookup failure yields a target with no optional controls.
                return build_model_target(provider, api, 'generate', model)
        generation = presentation['controls']['generate']
        operation = presentation['operations']['generate']
        provider_fields = _provider_config(provider, api)
        if model_scoped_parameters(provider, api):
            ratios = list(generation['aspect_ratios'])
            ratio_default = generation['default_aspect_ratio']
            sizes = list(generation['image_sizes'])
            size_default = generation['default_image_size']
        else:
            # API-wide registrations retain their established provider-wide
            # choices.  A complete model presentation may legitimately omit
            # those repeated facts (notably OpenRouter model records).
            ratios = aspect_ratios(provider, api)
            ratio_default = default_aspect_ratio(provider, api)
            sizes = image_sizes(provider, api)
            size_default = default_image_size(provider, api)
        controls: dict[str, Any] = {
            'availability': {
                'enabled': operation['available'],
                'reason': operation['unavailable_reason'],
            },
            'aspect_ratios': ratios,
            'default_aspect_ratio': ratio_default,
            'image_sizes': sizes,
            'default_image_size': size_default,
            'qualities': list(generation['qualities']),
            'default_quality': generation['default_quality'],
            'provider_config': {
                'supports_temperature': provider_fields.get(
                    'supports_temperature', False,
                ),
                'supports_system_prompt': provider_fields.get(
                    'supports_system_prompt', False,
                ),
                'extra_fields': list(generation['extra_fields']),
            },
        }
        return build_model_target(
            provider, api, 'generate', model, controls=controls,
        )

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
        system: str | None,
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
                system=system,
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
        metadata_warning = None
        generated_prompt = result.get('generated_prompt')
        prompt_enhancement = result.get('prompt_enhancement')

        # An enhanced result must use the client-side canonical metadata
        # writer even when the provider offered server-side embedding, so the
        # original and generated prompts are both represented.
        if (
            getattr(backend_cls, 'embeds_metadata_in_exif', False)
            or generated_prompt is not None
        ):
            metadata_warning = write_image_generation_exif_with_sidecar_fallback(
                [os.path.join(save_dir, f) for f in files],
                desc_file,
                files,
                model=actual_model,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                cost=cost,
                prompt=prompt,
                system=metadata_system,
                temperature=temperature,
                provider=provider,
                api=api,
                extra_params=_sanitize_image_metadata(extra_params) or None,
                generated_prompt=generated_prompt,
                prompt_enhancement=prompt_enhancement,
            )
            if metadata_warning:
                logger.warning('%s', metadata_warning)
        elif not (extra_params or {}).get('embed_exif_metadata'):
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
                    temperature=temperature,
                    provider=provider,
                    api=api,
                    extra_params=_sanitize_image_metadata(extra_params) or None,
                    generated_prompt=generated_prompt,
                    prompt_enhancement=prompt_enhancement,
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
            generated_prompt=generated_prompt,
        )

        payload: dict[str, Any] = {
            'files':         files,
            'cost':          cost,
            'model':         actual_model,
            'model_display': model_display(actual_model),
            'summary':       summary,
            'warning':       metadata_warning,
        }
        if generated_prompt is not None:
            payload['generated_prompt'] = generated_prompt
        return payload, 200

    def _generate_stream(
        self,
        prompt: str,
        model: str,
        aspect_ratio: str,
        image_size: str,
        temperature: float | None,
        system: str | None,
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
                    system, provider, api,
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
        provider_value = data.get('provider')
        if not isinstance(provider_value, str) or not provider_value.strip():
            return JsonResponse({'error': 'provider is required'}, status=400)
        provider_param = provider_value.strip()
        try:
            provider, api = normalize_provider_api(provider_param)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)

        try:
            model = resolve_action_model(
                data.get('model'), provider, api, operation='generate',
            )
        except LLemonImageParamError as e:
            return JsonResponse({'error': str(e)}, status=400)
        raw_temperature = data.get('temperature')
        raw_system = data.get('system')
        if raw_temperature in (None, ''):
            temperature = None
        else:
            try:
                temperature = float(raw_temperature)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'invalid temperature'}, status=400)
            if temperature < 0.0 or temperature > 2.0:
                return JsonResponse({'error': 'invalid temperature'}, status=400)
        system = raw_system.strip() if isinstance(raw_system, str) else None
        if system == '':
            system = None

        if not prompt:
            return JsonResponse({'error': 'prompt is required'}, status=400)
        try:
            valid_ratios = aspect_ratios(provider, api, model=model)
            ratio_default = default_aspect_ratio(provider, api, model=model)
            ratio_value = data.get('aspect_ratio')
            aspect_ratio = (
                ratio_value.strip() if isinstance(ratio_value, str) else ratio_value
            )
            if aspect_ratio in (None, ''):
                aspect_ratio = ratio_default or ''
            if valid_ratios and aspect_ratio and aspect_ratio not in valid_ratios:
                return JsonResponse({'error': 'invalid aspect_ratio'}, status=400)

            valid_sizes = image_sizes(provider, api, model=model)
            size_default = default_image_size(provider, api, model=model)
            size_value = data.get('image_size')
            image_size = size_value.strip() if isinstance(size_value, str) else size_value
            if image_size in (None, ''):
                image_size = size_default or ''
            if valid_sizes and image_size and image_size not in valid_sizes:
                return JsonResponse({'error': 'invalid image_size'}, status=400)

            implicit_extras = {
                'hide_watermark': True, 'embed_exif_metadata': True,
            }
            extra_input = {**data, **implicit_extras}
            extra_params: dict[str, Any] = extract_extra_params(
                provider, api, extra_input, model=model,
            )
            if model_scoped_parameters(provider, api):
                envelope = {
                    'provider', 'prompt', 'model', 'aspect_ratio', 'image_size',
                    'quality', 'temperature', 'system', 'stream', 'output_subdir',
                }
                submitted_extras = [
                    name for name in extra_input
                    if name not in envelope and name not in implicit_extras
                ]
                unsupported = unsupported_extra_params(
                    provider, api, submitted_extras, model=model,
                )
                if unsupported:
                    raise LLemonImageParamError(
                        f'unsupported parameter: {unsupported[0]}'
                    )
        except LLemonImageParamError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception:
            logger.exception('could not validate request against model information')
            return JsonResponse(
                {'error': 'could not validate request against model information'},
                status=502,
            )

        quality_val = data.get('quality')
        if isinstance(quality_val, str) and quality_val.strip():
            extra_params['quality'] = quality_val.strip()

        preflight_params: dict[str, Any] = {
            'prompt': prompt,
            'aspect_ratio': aspect_ratio or None,
            'image_size': image_size or None,
            'temperature': temperature,
            'system': system,
            **extra_params,
        }
        try:
            preflight_request(
                provider, api, model=model, operation='generate',
                params=preflight_params,
            )
        except LLemonImageParamError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception:
            logger.exception('could not validate request against model information')
            return JsonResponse(
                {'error': 'could not validate request against model information'},
                status=502,
            )

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
                    system, provider, api,
                    extra_params or None, output_subdir,
                ),
                content_type='application/x-ndjson',
            )
            resp['Cache-Control'] = 'no-cache'
            resp['X-Accel-Buffering'] = 'no'
            return resp

        payload, status = self._generate_result(
            prompt, model, aspect_ratio, image_size, temperature,
            system, provider, api,
            extra_params or None, output_subdir,
        )
        return JsonResponse(payload, status=status)

    def _models_json(self, request):
        provider_param = request.GET.get('provider', '').strip() or None
        target = request.GET.get('target', '').strip()
        notices: list[Any] = []
        try:
            provider, api = normalize_provider_api(provider_param)
            if target:
                if target != 'generate':
                    raise ValueError(f'unknown presentation target: {target!r}')
                model = request.GET.get('model', '').strip()
                if not model:
                    raise ValueError('model is required')
                presentation = self._image_model_target(
                    provider, api, model,
                    on_model_info_notice=notices.append,
                )
                return JsonResponse({
                    'presentation': presentation,
                    'notices': [_notice_dict(notice) for notice in notices],
                })
            raw_models = list_image_models_with_metadata(
                provider, api, on_model_info_notice=notices.append,
            )
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
            logger.exception('could not list image generation models for provider %s',
                             provider_param)
            return JsonResponse({'error': f'could not list models: {e}'}, status=502)
        try:
            edit_data = _edit_metadata(
                provider, api,
                requested_model=request.GET.get('selected_edit_model'),
                on_model_info_notice=notices.append,
            )
        except Exception as e:
            logger.exception('could not list edit models')
            return JsonResponse({'error': f'could not list edit models: {e}'},
                                status=502)
        requested_model = (
            request.GET.get('selected_generate_model')
            or request.GET.get('selected_model')
            or None
        )
        try:
            data = self._creator_data(
                provider, api, raw_models, edit_data,
                requested_model=requested_model,
                on_model_info_notice=notices.append,
            )
        except Exception as e:
            logger.exception('could not resolve image model presentation')
            return JsonResponse(
                {'error': f'could not resolve model presentation: {e}'},
                status=502,
            )
        return JsonResponse({
            'presentation': data['presentation'],
            'notices': [_notice_dict(notice) for notice in notices],
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

    def _parse_request_images(
        self, data: dict[str, Any],
    ) -> 'tuple[list[str], list[dict[str, Any]], JsonResponse | None]':
        """Parse one edit request's ordered image list into gallery
        filenames and placeholder ``LLemonImageEditInput`` entries, with no
        filesystem access.

        Each placeholder's ``source`` is the literal string ``'data:'`` —
        enough for ``normalize_edit_inputs()`` to classify it as
        ``data_url`` (every filename this endpoint later resolves becomes a
        real ``data:`` URL too) without reading or base64-encoding the
        actual file yet. This lets request shape/count/role be validated
        against the selected model's ``edit_inputs`` schema *before* any
        image is loaded, so an over-count or malformed request is rejected
        without paying to load images it will reject anyway, and a bad
        image later in the list can't produce a misleading "file not
        found" ahead of a count/role error that would have fired first.
        Call ``_resolve_request_image_sources()`` after
        ``normalize_edit_inputs()`` succeeds to substitute real data URLs.

        Accepts the provider-neutral ``images: [{filename, role?}]`` array
        (Task 13). A lone top-level ``filename`` string is accepted as
        input-only compatibility for one release, per
        specs/mediagen-image-spec.md's "Grove adoption" section, and is
        treated as a single-element array with no role.
        """
        raw_images = data.get('images')
        if raw_images is None:
            filename = str(data.get('filename') or '')
            raw_images = [{'filename': filename}] if filename else []
        if not isinstance(raw_images, list) or not raw_images:
            return [], [], JsonResponse({'error': 'images is required'}, status=400)
        filenames: list[str] = []
        placeholders: list[dict[str, Any]] = []
        for index, entry in enumerate(raw_images):
            if not isinstance(entry, dict):
                return [], [], JsonResponse(
                    {'error': f'images[{index}] must be an object'}, status=400,
                )
            filename = str(entry.get('filename') or '')
            if not filename:
                return [], [], JsonResponse(
                    {'error': f'images[{index}].filename is required'}, status=400,
                )
            filenames.append(filename)
            image: dict[str, Any] = {'source': 'data:'}
            if 'role' in entry:
                role_value = entry['role']
                # A non-string role (or one unhashable, e.g. a list) must
                # not reach normalize_edit_inputs()'s dict-keyed role
                # lookup unexamined; reject it here instead of silently
                # dropping it, which would let a garbage role submit as an
                # unroled image. An empty string is kept, not rejected
                # here: normalize_edit_inputs() itself already treats a
                # present-but-empty role as invalid for a named schema and
                # any role at all as invalid for an ordered one, so passing
                # it through lets that single rule enforce both cases
                # instead of duplicating it.
                if not isinstance(role_value, str):
                    return [], [], JsonResponse(
                        {'error': f'images[{index}].role must be a string'}, status=400,
                    )
                image['role'] = role_value
            placeholders.append(image)
        return filenames, placeholders, None

    def _resolve_request_image_sources(
        self, filenames: list[str], canonical_images: list[dict[str, Any]],
        source_dir: str,
    ) -> 'JsonResponse | None':
        """Replace each canonical image's placeholder source with its real
        gallery data URL, in the same order, after
        ``normalize_edit_inputs()`` has already validated request
        shape/count/role against the selected model. Returns an error
        response on the first unreadable file; the caller must not
        dispatch when this returns non-``None``.
        """
        for index, filename in enumerate(filenames):
            data_url, err = self._read_image_as_data_url(filename, source_dir=source_dir)
            if err:
                return JsonResponse(
                    {'error': err},
                    status=400 if err != 'media_dir not configured' else 500,
                )
            canonical_images[index]['source'] = data_url
        return None

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

        provider_value = data.get('provider')
        if not isinstance(provider_value, str) or not provider_value.strip():
            return JsonResponse({'error': 'provider is required'}, status=400)
        try:
            provider, api = normalize_provider_api(provider_value.strip())
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
        images: list[dict[str, Any]],
        source_filenames: list[str],
        media_dir: str,
        prompt: str,
        edit_model: str,
        aspect_ratio: str | None,
        image_size: str | None,
        safe_mode: bool | None,
        provider: str,
        api: str,
    ) -> 'tuple[dict[str, Any], int]':
        backend_cls = make_imagegen_backend(provider, api)
        backend = backend_cls(model=edit_model, log_dir=self._log_dir())
        edit_kwargs: dict[str, Any] = {
            'model':        edit_model,
            'aspect_ratio': aspect_ratio,
            'safe_mode':    safe_mode,
            # Grove does not yet collect data-handling-warning consent
            # (Task 13 Phase 2); this matches today's behavior, where a
            # warned transport (e.g. Segmind's qwen-image-edit upload path)
            # is unreachable through Grove either way.
            'accept_data_handling_warnings': False,
        }
        if image_size is not None:
            edit_kwargs['image_size'] = image_size
        try:
            result = backend.edit_images(images, prompt, **edit_kwargs)
        finally:
            backend.shutdown()
        if result.get('error'):
            err = result['error']
            return {'error': err['message']}, 502
        stem = os.path.splitext(source_filenames[0])[0] + '_edit'
        sidecar: dict[str, Any] = {
            'operation':    'edit',
            'prompt':       prompt,
            'model':        edit_model,
        }
        if len(source_filenames) == 1:
            sidecar['source'] = source_filenames[0]
        else:
            sidecar['sources'] = list(source_filenames)
        if aspect_ratio:
            sidecar['aspect_ratio'] = aspect_ratio
        if image_size:
            sidecar['image_size'] = image_size
        return self._save_operation_result(result, media_dir, stem, sidecar, backend_cls)

    def _edit_stream(self, images: list[dict[str, Any]], source_filenames: list[str],
                     media_dir: str, prompt: str, edit_model: str,
                     aspect_ratio: 'str | None', image_size: 'str | None',
                     safe_mode: 'bool | None', provider: str, api: str):
        q: queue.Queue[dict[str, Any]] = queue.Queue()

        def _worker() -> None:
            try:
                payload, status = self._edit_result(images, source_filenames, media_dir,
                                                    prompt, edit_model, aspect_ratio,
                                                    image_size, safe_mode, provider, api)
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

        provider_value = data.get('provider')
        if not isinstance(provider_value, str) or not provider_value.strip():
            return JsonResponse({'error': 'provider is required'}, status=400)
        try:
            provider, api = normalize_provider_api(provider_value.strip())
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        if not supports_edit(provider, api):
            return JsonResponse({'error': f'edit not supported by provider {provider!r}'},
                                status=400)

        filenames, raw_images, images_err = self._parse_request_images(data)
        if images_err is not None:
            return images_err

        prompt = (data.get('prompt') or '').strip()
        if not prompt:
            return JsonResponse({'error': 'prompt is required'}, status=400)

        model_value = data.get('model')
        edit_model = model_value.strip() if isinstance(model_value, str) else ''
        if not edit_model:
            return JsonResponse({'error': 'edit model is required'}, status=400)
        try:
            edit_meta = _edit_metadata(provider, api)
        except Exception as e:
            logger.exception('could not list edit models')
            return JsonResponse({'error': f'could not list edit models: {e}'},
                                status=502)
        # supports_edit(provider, api) was already confirmed True above, so a
        # successful _edit_metadata() call always carries at least one model;
        # discovery that comes back empty raises instead of returning [].
        valid_models = edit_meta['edit_models']
        selected_row = next(
            (row for row in edit_meta['edit_model_options']
             if row['id'] == edit_model), None,
        )
        if selected_row is None:
            return JsonResponse(
                {'error': f'invalid edit model; use one of: {", ".join(valid_models)}'},
                status=400,
            )
        _, enabled, reason = _operation_state(
            selected_row, 'edit_images', source_kind='data_url',
        )
        if not enabled:
            return JsonResponse(
                {'error': reason or 'edit model is unavailable'}, status=400,
            )
        edit_inputs_schema = selected_row['presentation']['edit_inputs']
        try:
            canonical_images = normalize_edit_inputs(raw_images, edit_inputs_schema)
        except LLemonImageEditInputError as e:
            return JsonResponse({'error': str(e)}, status=400)
        # Only now -- after request shape/count/role has been validated
        # against the selected model without touching the filesystem -- are
        # the actual gallery files read and base64-encoded.
        resolve_err = self._resolve_request_image_sources(
            filenames, canonical_images, source_dir,
        )
        if resolve_err is not None:
            return resolve_err
        row_controls = _edit_row_controls(provider, api, selected_row)
        valid_ratios = row_controls['aspect_ratios']
        aspect_ratio = (data.get('aspect_ratio') or '').strip() or None
        if aspect_ratio and valid_ratios and aspect_ratio not in valid_ratios:
            return JsonResponse({'error': 'invalid aspect_ratio'}, status=400)
        if not aspect_ratio:
            normalized_default = selected_row['presentation']['controls']['edit'][
                'default_aspect_ratio'
            ]
            if (
                isinstance(normalized_default, str)
                and normalized_default.strip()
            ):
                aspect_ratio = normalized_default.strip()
            elif 'auto' in valid_ratios:
                aspect_ratio = 'auto'
            elif valid_ratios:
                return JsonResponse(
                    {'error': (f'{provider} image edits resize to a fixed aspect '
                               f'ratio; set aspect_ratio '
                               f'(one of: {", ".join(valid_ratios)})')},
                    status=400,
                )
        valid_sizes = row_controls['image_sizes']
        image_size = (data.get('image_size') or '').strip() or None
        if image_size and not valid_sizes:
            if provider == 'venice':
                message = ('Venice single-image editing determines output size '
                           'from the source image; image_size is not accepted')
            else:
                message = f'provider {provider!r} image editing does not accept image_size'
            return JsonResponse({'error': message}, status=400)
        if valid_sizes:
            if not image_size:
                image_size = row_controls['default_image_size']
            if image_size not in valid_sizes:
                return JsonResponse(
                    {'error': f'invalid image_size; use one of: {", ".join(valid_sizes)}'},
                    status=400,
                )
        safe_mode_raw = data.get('safe_mode')
        safe_mode: bool | None = bool(safe_mode_raw) if safe_mode_raw is not None else None

        params: dict[str, Any] = {
            'prompt': prompt,
            'aspect_ratio': aspect_ratio,
            'image_size': image_size,
        }
        # Provider-schema preflight only knows one image; a multi-image
        # request (or one whose single image is pending an upload
        # transform) skips it here and relies on normalize_edit_inputs()
        # above plus the backend's own validate_request() at dispatch
        # instead. See _edit_input_transport_pending() and Task 8 Phase 5's
        # preflight-precedence fix (upgrade.md), which this mirrors.
        if (
            len(canonical_images) == 1
            and not _edit_input_transport_pending(edit_inputs_schema, canonical_images[0])
        ):
            params['image_input'] = canonical_images[0]['source']
        if safe_mode_raw is not None:
            params['safe_mode'] = safe_mode
        try:
            preflight_request(
                provider, api, model=edit_model, operation='edit', params=params,
            )
        except LLemonImageParamError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception:
            logger.exception('could not validate request against model information')
            return JsonResponse(
                {'error': 'could not validate request against model information'},
                status=502,
            )

        if not result_dir:
            return JsonResponse({'error': 'media_dir not configured'}, status=500)

        if data.get('stream'):
            resp = StreamingHttpResponse(
                self._edit_stream(canonical_images, filenames, result_dir, prompt,
                                  edit_model, aspect_ratio, image_size, safe_mode,
                                  provider, api),
                content_type='application/x-ndjson',
            )
            resp['Cache-Control'] = 'no-cache'
            resp['X-Accel-Buffering'] = 'no'
            return resp

        payload, status = self._edit_result(canonical_images, filenames, result_dir,
                                            prompt, edit_model, aspect_ratio, image_size,
                                            safe_mode, provider, api)
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

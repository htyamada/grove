"""llemon_djview - reusable Django views and URLs for the LLemon interface."""

import json
import logging
import os
import re
import tomllib
from pathlib import Path
import traceback
from urllib.parse import urlencode

import markdown as _markdown  # type: ignore[import-untyped]

from django.http import JsonResponse, StreamingHttpResponse  # type: ignore[import-untyped]
from django.shortcuts import redirect, render  # type: ignore[import-untyped]
from django.urls import reverse  # type: ignore[import-untyped]
from django.views.decorators.csrf import csrf_exempt  # type: ignore[import-untyped]
from django.views.decorators.http import require_POST  # type: ignore[import-untyped]

from hty7.config import AppConfig as _AppConfig
from hty7.config import ConfigError as _ConfigError

from hty7.llemon.persona import attach_context_estimate, summarize_history_estimates
from hty7.llemon.persona.config import Config, ConfigError
from hty7.llemon.persona.runtime import (
    apply_chat_overrides as _persona_apply_chat_overrides,
    load_persona_config as _persona_load_persona_config,
    read_config_summary as _persona_read_config_summary,
)
from hty7.llemon.persona.persona import Persona
from hty7.llemon.persona.service import Service
from hty7.llemon.persona.session import Session
from hty7.llemon.persona import discover
from hty7.llemon.core.history import History

_md = _markdown.Markdown(extensions=['fenced_code', 'tables', 'nl2br'])
_md_doc = _markdown.Markdown(extensions=['fenced_code', 'tables'])

logger = logging.getLogger(__name__)

_DEFAULT_CONF = '~/etc/llemon.conf'
_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DJVIEW_CONF = str(_ROOT / 'etc' / 'llemon_djview.conf')

_ConfigValue = str | list[str | dict[str, str]] | bool


def _render_md(text):
    _md.reset()
    return _md.convert(text)


def _render_md_doc(text):
    _md_doc.reset()
    return _md_doc.convert(text)


def media_settings(appconfig) -> dict[str, str | None]:
    """Initialize LLemon media backends and return Django settings values."""
    from hty7.llemon.mediagen import imagegen as _imagegen
    from hty7.llemon.mediagen import videogen as _videogen

    def _optional_path(key: str) -> str | None:
        if not hasattr(appconfig, 'get'):
            return None
        value = appconfig.get('llemon', 'mediagen', key)
        if not value:
            return None
        return os.path.expanduser(str(value))

    _imagegen.init(appconfig)
    _videogen.init(appconfig)
    return {
        'LLEMON_LOG_DIR': os.path.expanduser(discover.log_dir) if discover.log_dir else None,
        'LLEMON_IMAGEGEN_MEDIA_DIR': os.path.expanduser(_imagegen.get_media_dir()),
        'LLEMON_IMAGEGEN_LOG_DIR': (
            os.path.expanduser(_imagegen.get_log_dir()) or None
        ),
        'LLEMON_VIDEOGEN_MEDIA_DIR': os.path.expanduser(_videogen.get_media_dir()),
        'LLEMON_VIDEOGEN_LOG_DIR': (
            os.path.expanduser(_videogen.get_log_dir()) or None
        ),
        'LLEMON_IMAGE_ARCHIVE_DIR': _optional_path('image_archive'),
        'LLEMON_VIDEO_ARCHIVE_DIR': _optional_path('video_archive'),
    }


def _expand_djview_value(value: object, path: str, section: str, key: str) -> _ConfigValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return os.path.expanduser(value)
    if isinstance(value, list):
        items: list[str | dict[str, str]] = []
        for item in value:
            if isinstance(item, str):
                items.append(os.path.expanduser(item))
            elif isinstance(item, dict):
                items.append({
                    str(k): os.path.expanduser(v) if isinstance(v, str) else str(v)
                    for k, v in item.items()
                })
            else:
                raise _ConfigError(
                    f"{path}: [{section}] {key!r}: list elements must be strings or inline tables"
                )
        return items
    raise _ConfigError(
        f"{path}: [{section}] {key!r}: value must be a string, array, or boolean"
    )


def _parse_djview_conf(path: str) -> dict[str, dict[str, _ConfigValue]]:
    """Parse the grove-local llemon_djview.conf overlay."""
    try:
        with open(path, 'rb') as f:
            nested = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise _ConfigError(f"{path}: {e}") from e

    parsed: dict[str, dict[str, _ConfigValue]] = {}
    for variant, projects in nested.items():
        if not isinstance(projects, dict):
            raise _ConfigError(f"{path}: top-level key {variant!r} is not a table")
        for project, layers in projects.items():
            if not isinstance(layers, dict):
                raise _ConfigError(f"{path}: [{variant}.{project}] is not a table")
            for layer, values in layers.items():
                if not isinstance(values, dict):
                    raise _ConfigError(f"{path}: [{variant}.{project}.{layer}] is not a table")
                section = f"{variant}.{project}.{layer}"
                parsed[section] = {
                    str(key): _expand_djview_value(value, path, section, str(key))
                    for key, value in values.items()
                }
    return parsed


def _merge_djview_conf(
    base: dict[str, dict[str, _ConfigValue]],
    overlay: dict[str, dict[str, _ConfigValue]],
) -> dict[str, dict[str, _ConfigValue]]:
    merged = {section: dict(values) for section, values in base.items()}
    for section, values in overlay.items():
        merged.setdefault(section, {}).update(values)
    return merged


def _setup_startup_logging(appconfig) -> None:
    """Capture LLemon startup notifications (e.g. key-loading) in a log file.

    Django applies its LOGGING dictConfig only after the settings module is
    fully imported, but django_settings() loads API keys (via discover.init ->
    core.init -> load_keys) during that import.  Without an early handler, the
    key-loading notifications emitted by hty7.llemon.core.keys are lost (they
    only reach stderr via Python's last-resort handler).  Attach a FileHandler
    to the root logger -- before keys load -- so library loggers propagate into
    a log file.  Django's later dictConfig replaces this handler for runtime.
    """
    if not hasattr(appconfig, 'get'):
        return
    log_dir = str(
        appconfig.get('llemon', 'persona', 'log_dir')
        or appconfig.get('llemon', 'core', 'log_dir')
        or ''
    )
    if not log_dir:
        return
    try:
        expanded = os.path.expanduser(log_dir)
        os.makedirs(expanded, exist_ok=True)
        handler = logging.FileHandler(
            os.path.join(expanded, 'llemon-djview.log'), encoding='utf-8',
        )
        handler.setLevel(logging.WARNING)
        handler.setFormatter(
            logging.Formatter('%(asctime)s %(levelname)-8s %(name)s: %(message)s'),
        )
        root = logging.getLogger()
        root.addHandler(handler)
        if root.level == logging.NOTSET or root.level > logging.WARNING:
            root.setLevel(logging.WARNING)
    except OSError:
        pass


def django_settings(variant: str, conf_path: str = _DEFAULT_CONF) -> dict[str, str | None]:
    """Load installed LLemon config for variant and return Django settings values."""
    appconfig = _AppConfig(os.path.expanduser(conf_path), variant)
    if os.path.exists(_DEFAULT_DJVIEW_CONF):
        overlay = _parse_djview_conf(_DEFAULT_DJVIEW_CONF)
        appconfig._data = _merge_djview_conf(appconfig._data, overlay)
    # Configure logging before discover.init() loads API keys, so key-loading
    # notifications are captured rather than lost.
    _setup_startup_logging(appconfig)
    discover.init(appconfig)
    return media_settings(appconfig)


def _load_display_history_from_file(path):
    """Return (history_for_template, turns_count) from a v2 .jsonl history file."""
    history = []
    try:
        for message in History.display_messages(path):
            history.append({
                'role': message['role'],
                'content': message['content'],
                'html': _render_md(str(message['content'])),
                'turn_id': message.get('turn_id'),
            })
    except Exception:
        logger.exception('could not load display history from %s', path)
    return history


def _read_history_name(path: str) -> str:
    """Return the explicit description from a v2 .jsonl history header, or ''."""
    return History.history_description(path)


def _read_history_title(path: str) -> str:
    """Return the title from a v2 .jsonl history header, or ''."""
    return History.history_title(path)


def _load_history_turns_from_file(path: str) -> list[dict]:
    """Return raw turns from a v2 .jsonl history file."""
    try:
        return History.load_turns(path, attach_estimates_to_header=True)
    except Exception:
        logger.exception('could not load raw history turns from %s', path)
        return []


def _read_config_summary(path: str, fallback_name: str) -> tuple[str, str]:
    """Return the config's canonical name and title for selection pages."""
    try:
        return _persona_read_config_summary(path, fallback_name)
    except Exception:
        logger.exception('could not read config summary for %s', fallback_name)
        return discover.config_base(fallback_name), discover.invalid_description_label(path)


def _parse_temperature_override(value) -> float | None:
    if value in (None, ''):
        return None
    return float(value)


def _parse_bool_override(value) -> bool | None:
    if value in (None, ''):
        return None
    return str(value).lower() not in ('0', 'false', 'no', 'off')


def _apply_chat_overrides(config, *, temperature=None, write_history=None,
                          debug=None) -> None:
    _persona_apply_chat_overrides(
        config,
        temperature=temperature,
        write_history=write_history,
        debug=debug,
    )


def _manual_query_params(*, provider=None, model=None) -> dict[str, str]:
    params: dict[str, str] = {}
    if provider not in (None, ''):
        params['provider'] = str(provider)
    if model not in (None, ''):
        params['model'] = str(model)
    return params


def _manual_selection_active(provider=None, model=None) -> bool:
    return provider not in (None, '') or model not in (None, '')


def _manual_selection_ready(provider=None, model=None) -> bool:
    return provider not in (None, '') and model not in (None, '')


def _load_persona_config(config_path, service_name=None, *,
                         provider=None, model=None,
                         history_path=None, start_file_path=None):
    return _persona_load_persona_config(
        config_path,
        service_name,
        provider=provider,
        model=model,
        history_path=history_path,
        start_file_path=start_file_path,
    )


def _override_query_params(*, temperature=None, write_history=None, debug=None) -> dict[str, str]:
    params: dict[str, str] = {}
    if temperature not in (None, ''):
        params['temperature'] = str(temperature)
    if write_history not in (None, ''):
        params['write_history'] = str(write_history).lower()
    if debug not in (None, ''):
        params['debug'] = str(debug).lower()
    return params


def _macro_route_command(command: str) -> str | None:
    cmd = command.strip()
    if cmd in ('/help', '/?'):
        return None
    if cmd == 'connect':
        return 'start'
    if cmd.startswith('set type') and (len(cmd) == 8 or cmd[8] == ' '):
        return 'type' if cmd[8:].strip() else None
    if cmd.startswith('set config') and (len(cmd) == 10 or cmd[10] == ' '):
        return 'config' if cmd[10:].strip() else None
    if cmd.startswith('set service') and (len(cmd) == 11 or cmd[11] == ' '):
        return 'service' if cmd[11:].strip() else None
    return None


def _history_label(fname: str, base: str) -> str:
    """Extract the timestamp label from a v2 .jsonl history filename."""
    stem = fname[:-len('.jsonl')] if fname.endswith('.jsonl') else fname
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in base)
    return stem[len(safe) + 1:] if stem.startswith(safe + '_') else stem


def _history_display_name(path: str, fname: str | None = None,
                          base: str | None = None) -> str:
    from hty7.llemon.core.history import History
    return (History.history_name(path)
            or (_history_label(fname, base or '') if fname else '')
            or os.path.basename(path))


def _history_macro_name(path: str, fname: str | None = None) -> str:
    from hty7.llemon.core.history import History
    name = History.history_name(path)
    if name:
        return name
    match = re.search(r'_(h[0-9]{6})_', fname or os.path.basename(path))
    if match:
        name = History.default_history_name(match.group(1))
        if name:
            return name
    return fname or os.path.basename(path)


def _running_page_title(config_path: str, service_name: str,
                        start_fname: str | None,
                        history_fname: str | None) -> str:
    """Return the setup page title equivalent for an active Django session."""
    type_id = Config.read_type(config_path)
    parts = [discover.type_descr(type_id), Config.display_name(config_path)]
    if history_fname:
        history_path = discover.resolve_history_path(history_fname)
        base = discover.config_base(os.path.basename(config_path))
        parts.append(f"history {_history_display_name(history_path, history_fname, base)}")
    elif start_fname:
        start_path = discover.resolve_start_file(config_path, start_fname)
        parts.append(f"start {discover.start_file_name(start_path) if start_path is not None else start_fname}")
    else:
        parts.append('New')
    if service_name:
        parts.append(discover.service_display_name(service_name))
    return f"{' / '.join(parts)} / Running"


class LLemonViewSet:
    """Django views for the LLemon chat interface, bound to a specific app namespace."""

    def __init__(self, template_prefix: str, url_namespace: str, *, base_nav=None,
                 nav=None, nav_suffix=None):
        self._tp         = template_prefix
        self._ns         = url_namespace
        self._base_nav   = base_nav
        self._nav_prefix = list(nav) if nav else []
        self._nav_suffix = list(nav_suffix) if nav_suffix else []
        self.stream           = csrf_exempt(require_POST(self._stream))
        self.render_markdown  = csrf_exempt(require_POST(self._render_markdown))
        self.edit_history     = csrf_exempt(require_POST(self._edit_history))
        self.delete_history   = csrf_exempt(require_POST(self._delete_history))
        self.set_history_name = csrf_exempt(require_POST(self._set_history_name))
        self.set_history_title = csrf_exempt(require_POST(self._set_history_title))

    def _t(self, name):
        return f'{self._tp}/{name}'

    def _u(self, name):
        return reverse(f'{self._ns}:{name}')

    def _build_nav(self):
        nav = [
            {'name': 'Models',   'url': self._u('models')},
            {'name': 'Services', 'url': self._u('services')},
        ]
        return self._nav_prefix + nav + self._nav_suffix

    def _ctx(self, title, extra):
        ctx = {'title': title, 'nav': self._build_nav()}
        if self._base_nav is not None:
            ctx['base_nav'] = self._base_nav
        ctx.update(extra)
        return ctx

    def _setup_status_rows(self, *, type_id='', type_title='', type_url=None,
                           config_value='', config_title='', config_url=None,
                           init_value='', init_title='', init_url=None,
                           service_value='', service_title='',
                           service_url=None):
        return [
            {
                'field': 'Type',
                'value': type_id,
                'title': type_title,
                'url': type_url,
            },
            {
                'field': 'Config',
                'value': config_value,
                'title': config_title,
                'url': config_url,
            },
            {
                'field': 'Init',
                'value': init_value,
                'title': init_title,
                'url': init_url,
            },
            {
                'field': 'Service',
                'value': service_value,
                'title': service_title,
                'url': service_url,
            },
        ]

    def _macro_type_id(self, full_path):
        try:
            _metadata, commands = discover.load_macro_file(full_path)
        except Exception:
            return None
        for command in commands:
            command = command.strip()
            if command.startswith('set type') and (
                len(command) == len('set type') or command[len('set type')] == ' '
            ):
                return command[len('set type'):].strip() or None
        return None

    def _macro_groups(self, macro_rows, type_display):
        groups_by_type = {}
        for macro in macro_rows:
            type_id = macro.get('type') or ''
            group = groups_by_type.setdefault(type_id, {
                'type': type_id,
                'title': type_display.get(type_id, type_id) if type_id else 'Other',
                'macros': [],
            })
            group['macros'].append(macro)

        known_groups = [
            groups_by_type[type_id]
            for type_id in type_display
            if type_id in groups_by_type
        ]
        extra_groups = sorted(
            (group for type_id, group in groups_by_type.items() if type_id not in type_display),
            key=lambda group: group['title'].lower(),
        )
        return known_groups + extra_groups

    # ------------------------------------------------------------------ #

    def _index_extra(self, error=None):
        types = discover.find_types()
        macros = discover.find_macro_files()
        type_display = {type_id: display for type_id, display in types}
        status_rows = self._setup_status_rows()
        type_rows = [
            {
                'name': type_id,
                'title': type_display,
                'url': (
                    None if discover.is_invalid_description_file(discover.resolve_path(f"{type_id}.type.json"))
                    else self._u('configs') + '?' + urlencode({'type': type_id})
                ),
                'invalid': discover.is_invalid_description_file(discover.resolve_path(f"{type_id}.type.json")),
                'error': discover.invalid_description_error(discover.resolve_path(f"{type_id}.type.json")) or '',
            }
            for type_id, type_display in types
        ]
        macro_rows = [
            {
                'display': display_name,
                'fname': fname,
                'url': (
                    None if discover.is_invalid_description_file(full_path)
                    else self._u('session') + '?' + urlencode({'file': fname})
                ),
                'invalid': discover.is_invalid_description_file(full_path),
                'error': discover.invalid_description_error(full_path) or '',
                'type': self._macro_type_id(full_path),
            }
            for display_name, fname, full_path in macros
        ]
        return {
            'types':       types,
            'type_rows':   type_rows,
            'status_rows': status_rows,
            'macros':      macro_rows,
            'macro_groups': self._macro_groups(macro_rows, type_display),
            'error':       error,
        }

    def index(self, request):
        return render(request, self._t('index.html'),
                      self._ctx('LLemon Persona', self._index_extra()))

    def session(self, request):
        fname = request.GET.get('file', '')
        if not fname or '/' in fname or not fname.endswith('.llmac'):
            return redirect(self._u('index'))

        session_path = discover.resolve_path(fname)
        if not os.path.exists(session_path):
            return redirect(self._u('index'))

        try:
            session = Session()
            last_route = None

            def remember_route(command: str, _result: str | None) -> None:
                nonlocal last_route
                route = _macro_route_command(command)
                if route is not None:
                    last_route = route

            explicit_init, do_start = session.configure_from_macro_file(
                session_path, command_applied=remember_route)
        except ConfigError as e:
            logger.info('macro failed for %s: %s', fname, e)
            return render(request, self._t('index.html'),
                          self._ctx('LLemon Persona', self._index_extra(str(e))))

        if not session.type_id:
            return redirect(self._u('index'))

        params: dict[str, str] = {'type': session.type_id}
        params.update(_override_query_params(
            temperature=session.temperature_override,
            write_history=session.write_history_override,
            debug=session.debug_override,
        ))
        if session.config_fname:
            params['config'] = session.config_fname
        if session.service_name:
            params['service'] = session.service_name
        if explicit_init or do_start:
            if session.history_path:
                params['init'] = 'history'
                params['history'] = os.path.basename(session.history_path)
            elif session.start_file_path:
                params['init'] = 'start'
                params['start'] = os.path.basename(session.start_file_path)
            else:
                params['init'] = 'new'

        if last_route == 'type':
            params = {'type': session.type_id}
        elif last_route == 'config':
            params = {'type': session.type_id}
            if session.config_fname:
                params['config'] = session.config_fname
        elif last_route == 'init':
            params.pop('service', None)
        elif last_route == 'start' and session.config_fname and session.service_name:
            chat_params: dict[str, str] = {
                'config':  session.config_fname,
                'service': session.service_name,
            }
            chat_params.update(_override_query_params(
                temperature=session.temperature_override,
                write_history=session.write_history_override,
                debug=session.debug_override,
            ))
            if session.history_path:
                chat_params['history'] = os.path.basename(session.history_path)
            elif session.start_file_path:
                chat_params['start'] = os.path.basename(session.start_file_path)
            return redirect(self._u('chat') + '?' + urlencode(chat_params))

        return redirect(self._u('configs') + '?' + urlencode(params))

    def configs(self, request):
        type_id = request.GET.get('type')
        if not type_id:
            return redirect(self._u('index'))

        error = request.GET.get('error') or None
        config_fname = request.GET.get('config', '')
        service_name = request.GET.get('service', '')
        service_choice = request.GET.get('service_choice', '')
        init_mode = request.GET.get('init', '')
        start_fname = request.GET.get('start', '')
        history_fname = request.GET.get('history', '')
        init_choice = request.GET.get('init_choice', '')
        temperature = request.GET.get('temperature', '')
        write_history = request.GET.get('write_history', '')
        debug = request.GET.get('debug', '')
        manual_provider = request.GET.get('provider', '')
        manual_model = request.GET.get('model', '')
        if init_choice:
            init_mode = ''
            start_fname = ''
            history_fname = ''
            if init_choice == 'new':
                init_mode = 'new'
            elif init_choice.startswith('start:'):
                init_mode = 'start'
                start_fname = init_choice[len('start:'):]
            elif init_choice.startswith('history:'):
                init_mode = 'history'
                history_fname = init_choice[len('history:'):]
            else:
                error = f"Invalid start mode selection: {init_choice}"
        override_params = _override_query_params(
            temperature=temperature,
            write_history=write_history,
            debug=debug,
        )
        manual_params = _manual_query_params(
            provider=manual_provider,
            model=manual_model,
        )
        if service_choice:
            service_name = service_choice
        session = Session()
        try:
            session.set_type(type_id)
        except ConfigError as exc:
            return render(request, self._t('index.html'),
                          self._ctx('LLemon Persona', self._index_extra(str(exc))))
        type_display = discover.type_descr(type_id)
        config_list  = []

        def render_config_list(message=None):
            status_rows = self._setup_status_rows(
                type_id=type_id,
                type_title=type_display,
                type_url=self._u('index'),
            )
            for _display_name, fname, full_path in session.list_configs():
                name, title = _read_config_summary(full_path, fname)
                invalid = discover.is_invalid_description_file(full_path)
                config_list.append({
                    'name':  name,
                    'title': title,
                    'fname': fname,
                    'url':   None if invalid else self._u('configs') + '?' + urlencode({
                    'type':   type_id,
                        'config': fname,
                        **override_params,
                    }),
                    'invalid': invalid,
                    'error': discover.invalid_description_error(full_path) or '',
                })
            return render(request, self._t('configs.html'), self._ctx('LLemon Persona — Select config', {
                'type_id':      type_id,
                'type_display': type_display,
                'configs':      config_list,
                'selected':     None,
                'status_rows':  status_rows,
                'error':        message or error,
            }))

        if not config_fname:
            return render_config_list()

        if '/' in config_fname:
            return render_config_list('Invalid config filename.')

        config_path = discover.resolve_path(config_fname)
        try:
            session.set_config(config_path)
        except ConfigError as exc:
            return render_config_list(str(exc))

        display_name = session.config_display
        fname = session.config_fname
        full_path = session.config_path
        services = session.list_services()
        manual_selection = _manual_selection_active(manual_provider, manual_model)
        manual_ready = _manual_selection_ready(manual_provider, manual_model)
        manual_models: list[str] = []
        manual_model_error = None
        if manual_provider:
            try:
                manual_models = discover.list_models(manual_provider)
            except Exception as exc:
                logger.exception('could not list models for provider %s', manual_provider)
                manual_model_error = str(exc)
        if manual_model and manual_model not in manual_models:
            manual_model = ''
            manual_params = _manual_query_params(provider=manual_provider, model=manual_model)
            manual_ready = False
        if not init_mode and not start_fname and not history_fname:
            init_mode = 'new'
        if manual_selection:
            service_name = ''
        if not service_name and not manual_selection:
            for svc_name, svc_display, _provider, _model, _caps in services:
                if not svc_display.startswith('Invalid:'):
                    service_name = svc_name
                    break
        if service_name and not manual_selection:
            try:
                session.set_service(service_name)
            except ConfigError as exc:
                error = str(exc)
                service_name = ''
        if init_mode not in ('', 'new', 'start', 'history'):
            error = f"Invalid init mode: {init_mode}"
            init_mode = ''
        for display_name, fname, full_path in [(display_name, fname, full_path)]:
            base     = discover.config_base(fname)
            name, title = _read_config_summary(full_path, fname)
            history_entries = []
            start_entries = []
            init_entries = []
            selected_init = None
            start_lookup = {}
            history_lookup = {}
            service_display = next((s[1] for s in services if s[0] == service_name), '')
            if manual_ready:
                service_display = f'{manual_provider} / {manual_model}'
            status_rows = self._setup_status_rows(
                type_id=type_id,
                type_title=type_display,
                type_url=self._u('index'),
                config_value=base,
                config_title=display_name,
                config_url=self._u('configs') + '?' + urlencode({'type': type_id}),
                service_value=service_name,
                service_title=service_display,
            )
            if manual_ready:
                status_rows[3]['value'] = 'manual'
            init_entries.append({
                'name': 'new',
                'title': 'New session',
                'choice_value': 'new',
                'checked': init_mode == 'new',
                'url': self._u('configs') + '?' + urlencode({
                    'type':   type_id,
                    'config': fname,
                    'init':   'new',
                    **({'service': service_name} if service_name and not manual_selection else {}),
                    **override_params,
                    **manual_params,
                }),
            })
            for hfname, hpath in discover.list_history_files(fname):
                hist_name = _history_display_name(hpath, hfname, base)
                h_preview = discover.history_preview(hpath)[:120]
                history_lookup[hfname] = {
                    'value': f'history {hist_name}',
                    'title': h_preview,
                }
                history_entries.append({
                    'fname':   hfname,
                    'choice_value': f'history:{hfname}',
                    'number':  hist_name,
                    'preview': h_preview,
                    'checked': init_mode == 'history' and history_fname == hfname,
                    'url':     self._u('configs') + '?' + urlencode({
                        'type':    type_id,
                        'config':  fname,
                        'init':    'history',
                        'history': hfname,
                        **({'service': service_name} if service_name and not manual_selection else {}),
                        **override_params,
                        **manual_params,
                    }),
                })
            start_entries = []
            for sf_fname, sf_full in discover.find_start_files(full_path):
                invalid = discover.is_invalid_description_file(sf_full)
                start_entries.append({
                    'fname':   sf_fname,
                    'choice_value': f'start:{sf_fname}',
                    'name':    sf_fname if invalid else discover.start_file_name(sf_full),
                    'display': discover.invalid_description_label(sf_full) if invalid else discover.start_file_display(sf_full),
                    'checked': init_mode == 'start' and start_fname == sf_fname,
                    'url':     None if invalid else self._u('configs') + '?' + urlencode({
                        'type':   type_id,
                        'config': fname,
                        'init':   'start',
                        'start':  sf_fname,
                        **({'service': service_name} if service_name and not manual_selection else {}),
                        **override_params,
                        **manual_params,
                    }),
                    'invalid': invalid,
                    'error': discover.invalid_description_error(sf_full) or '',
                })
            for sf in start_entries:
                if sf['invalid']:
                    init_entries.append({
                        'name':  f"file {sf['name']}",
                        'title': sf['display'],
                        'choice_value': sf['choice_value'],
                        'checked': sf['checked'],
                        'url':   None,
                        'invalid': True,
                        'error': sf['error'],
                    })
                    continue
                start_lookup[sf['fname']] = {
                    'value': f"file {sf['name']}",
                    'title': sf['display'],
                }
                init_entries.append({
                    'name':  f"file {sf['name']}",
                    'title': sf['display'],
                    'choice_value': sf['choice_value'],
                    'checked': sf['checked'],
                    'url':   sf['url'],
                })
            for h in history_entries:
                init_entries.append({
                    'name':  f"history {h['number']}",
                    'title': h['preview'],
                    'choice_value': h['choice_value'],
                    'checked': h['checked'],
                    'url':   h['url'],
                    'history_fname': h['fname'],
                })
            if init_mode == 'new':
                selected_init = {
                    'value': 'new',
                    'title': 'New session',
                }
            elif init_mode == 'start' and start_fname in start_lookup:
                selected_init = start_lookup[start_fname]
            elif init_mode == 'history' and history_fname in history_lookup:
                selected_init = history_lookup[history_fname]
            elif init_mode:
                error = f"Selected {init_mode} entry is not available."
                init_mode = ''
            if selected_init:
                status_rows[2]['value'] = selected_init['value']
                status_rows[2]['title'] = selected_init['title']
                init_reset_params = {
                    'type':   type_id,
                    'config': fname,
                    **override_params,
                    **manual_params,
                }
                if service_name and not manual_selection:
                    init_reset_params['service'] = service_name
                status_rows[2]['url'] = self._u('configs') + '?' + urlencode(init_reset_params)
                if service_name or manual_ready:
                    chat_params = {
                        'config':  fname,
                        **override_params,
                    }
                    if service_name and not manual_selection:
                        chat_params['service'] = service_name
                    if manual_ready:
                        chat_params.update(_manual_query_params(
                            provider=manual_provider,
                            model=manual_model,
                        ))
                    if init_mode == 'start':
                        chat_params['start'] = start_fname
                    elif init_mode == 'history':
                        chat_params['history'] = history_fname
                    selected_init['chat_params'] = chat_params
                    selected_init['url'] = self._u('chat') + '?' + urlencode(chat_params)
            if service_name or manual_selection:
                service_reset_params = {
                    'type':   type_id,
                    'config': fname,
                    **override_params,
                }
                if init_mode:
                    service_reset_params['init'] = init_mode
                if start_fname:
                    service_reset_params['start'] = start_fname
                if history_fname:
                    service_reset_params['history'] = history_fname
                status_rows[3]['url'] = self._u('configs') + '?' + urlencode(service_reset_params)
            abstract = ''
            try:
                with open(full_path) as _f:
                    _cfg_data = json.load(_f)
                abstract_fname = _cfg_data.get('abstract') or ''
                if abstract_fname:
                    if os.path.isabs(abstract_fname):
                        abstract_path = abstract_fname
                    else:
                        abstract_path = os.path.normpath(
                            os.path.join(os.path.dirname(full_path), abstract_fname)
                        )
                    if os.path.exists(abstract_path):
                        with open(abstract_path) as _af:
                            abstract = _render_md_doc(_af.read())
            except Exception:
                logger.exception('could not load abstract for %s', fname)
            overrides = None
            override_error = None
            if selected_init and (service_name or manual_ready):
                try:
                    override_config = _load_persona_config(
                        full_path,
                        service_name if not manual_selection else None,
                        provider=manual_provider if manual_ready else None,
                        model=manual_model if manual_ready else None,
                    )
                    _apply_chat_overrides(
                        override_config,
                        temperature=temperature,
                        write_history=write_history,
                        debug=debug,
                    )
                    overrides = {
                        'temperature': override_config.service.temperature,
                        'write_history': override_config.write_history,
                        'debug': override_config.debug,
                    }
                except Exception as exc:
                    logger.exception('could not resolve override defaults for %s', fname)
                    override_error = str(exc)
            config_list.append({
                'name':        name,
                'title':       title,
                'display':     display_name,
                'fname':       fname,
                'abstract':    abstract,
                'services':    [{
                    'name':     s[0],
                    'choice_value': s[0],
                    'checked':  s[0] == service_name and not manual_selection,
                    'display':  s[1],
                    'features': discover.service_features_label(s[3], s[4]),
                    'provider': s[2],
                    'model':    s[3],
                    'invalid':  s[1].startswith('Invalid:'),
                    'url':      None if s[1].startswith('Invalid:') else self._u('configs') + '?' + urlencode({
                        'type':    type_id,
                        'config':  fname,
                        **({'init': init_mode} if init_mode else {}),
                        **({'start': start_fname} if start_fname else {}),
                        **({'history': history_fname} if history_fname else {}),
                        **override_params,
                        'service': s[0],
                    }),
                } for s in services],
                'service_name': service_name,
                'service_display': next((s[1] for s in services if s[0] == service_name), ''),
                'service_provider': manual_provider or next((s[2] for s in services if s[0] == service_name), ''),
                'service_model': manual_model or next((s[3] for s in services if s[0] == service_name), ''),
                'status_rows': status_rows,
                'init_entries': init_entries,
                'selected_init': selected_init,
                'init_mode': init_mode,
                'start_fname': start_fname,
                'history_fname': history_fname,
                'query_overrides': override_params,
                'manual_query_params': manual_params,
                'manual_provider': manual_provider,
                'manual_model': manual_model,
                'manual_models': manual_models,
                'manual_model_error': manual_model_error,
                'providers': discover.find_providers(),
                'overrides': overrides,
                'override_error': override_error,
                'history':     history_entries,
                'start_files': start_entries,
            })
        selected = config_list[0] if config_list else None
        page_title = 'LLemon Persona - Select config'
        if selected:
            page_title = 'LLemon Persona — Connect Session'
        return render(request, self._t('configs.html'), self._ctx(page_title, {
            'type_id':      type_id,
            'type_display': type_display,
            'configs':      config_list,
            'selected':     selected,
            'error':        error,
        }))

    def _load_chat_config(self, config_path, service_name,
                          *, provider=None, model=None,
                          temperature=None, write_history=None, debug=None):
        try:
            config = _load_persona_config(
                config_path,
                service_name,
                provider=provider,
                model=model,
            )
            _apply_chat_overrides(config, temperature=temperature,
                                  write_history=write_history, debug=debug)
            persona = Persona(config)
            return {
                'header_text':    persona.header or '',
                'user_tag':       persona.user_tag or 'You',
                'assistant_tag':  persona.assistant_tag or 'Assistant',
                'e2ee':           persona.is_e2ee(),
                'tee':            persona.is_tee(),
                'edit_responses': bool(getattr(persona, 'edit_responses', False)),
                'write_history':   bool(getattr(persona, 'write_history', True)),
                'download_field':  getattr(persona, 'download_field', None),
                'state':          persona.get_state() if persona.has_structured_output() else None,
                'debug':          persona.show_state(),
                'error':          None,
            }
        except Exception as e:
            logger.exception('could not build chat config for %s', config_path)
            return {'error': str(e)}

    def chat(self, request):
        config_fname = request.GET.get('config')
        history_fname = request.GET.get('history')
        service_name  = request.GET.get('service')
        start_fname   = request.GET.get('start')
        manual_provider = request.GET.get('provider', '')
        manual_model = request.GET.get('model', '')
        temperature   = request.GET.get('temperature', '')
        write_history = request.GET.get('write_history', '')
        debug         = request.GET.get('debug', '')
        manual_ready = _manual_selection_ready(manual_provider, manual_model)

        if not config_fname or (not service_name and not manual_ready):
            return redirect(self._u('index'))

        config_path = discover.resolve_path(config_fname)
        if not os.path.exists(config_path):
            return redirect(self._u('index'))

        display_name = Config.display_name(config_path)
        type_id      = Config.read_type(config_path)
        config_id    = discover.config_id(config_path)
        effective_provider = manual_provider if manual_ready else ''
        effective_model = manual_model if manual_ready else ''
        if not manual_ready and service_name:
            try:
                services = discover.list_services(config_path)
            except Exception:
                logger.exception('could not list services for %s', config_path)
            else:
                for svc_name, _svc_display, svc_provider, svc_model, *_rest in services:
                    if svc_name == service_name:
                        effective_provider = svc_provider or ''
                        effective_model = svc_model or ''
                        break
        chat_config  = self._load_chat_config(
            config_path, service_name,
            provider=manual_provider if manual_ready else None,
            model=manual_model if manual_ready else None,
            temperature=temperature,
            write_history=write_history,
            debug=debug,
        )
        page_title   = 'LLemon Persona — Running session'

        if chat_config['error']:
            return render(request, self._t('system.html'), self._ctx(f'Error — {page_title}', {
                'display_name': display_name,
                'config_fname': config_fname,
                'service_name': service_name,
                'system_text':  None,
                'error':        chat_config['error'],
            }))

        if history_fname:
            history_path = discover.resolve_history_path(history_fname)
            start_fname  = None
        else:
            history_path = discover.new_history_path_for_config(config_path)
            if history_path is None:
                return redirect(self._u('index'))
            history_fname = os.path.basename(history_path)

        history = []
        initial_history_data: list[dict] = []
        initial_history_summary: dict = {}
        history_mtime = None
        session_name = ''
        session_title = ''
        if os.path.exists(history_path):
            try:
                history = _load_display_history_from_file(history_path)
                initial_history_data = _load_history_turns_from_file(history_path)
                initial_history_summary = summarize_history_estimates(initial_history_data)
                history_mtime = os.path.getmtime(history_path)
                session_name = _read_history_name(history_path)
                session_title = _read_history_title(history_path)
            except Exception:
                logger.exception('could not load history for chat page: %s', history_path)

        display_title = session_title or session_name
        if display_title:
            page_title = f'LLemon Persona — Running {display_title}'

        start_message = ''
        if start_fname and '/' not in start_fname and not history:
            start_path = discover.resolve_start_file(config_path, start_fname)
            if start_path:
                start_message = discover.start_file_body(start_path)

        history_macro_name = _history_macro_name(history_path, history_fname)
        start_macro_name = ''
        if start_fname:
            start_path = discover.resolve_start_file(config_path, start_fname)
            if start_path:
                try:
                    start_macro_name = discover.start_file_name(start_path) or start_fname
                except ConfigError:
                    start_macro_name = start_fname

        return render(request, self._t('chat.html'), self._ctx(page_title, {
            'config_fname':  config_fname,
            'config_id':     config_id,
            'history_fname': history_fname,
            'history_macro_name': history_macro_name,
            'service_name':  service_name,
            'provider_name': effective_provider,
            'model_name': effective_model,
            'display_name':  display_name,
            'page_title':    page_title,
            'type_id':       type_id,
            'header_text':   chat_config['header_text'],
            'user_tag':      chat_config['user_tag'],
            'assistant_tag': chat_config['assistant_tag'],
            'e2ee':          chat_config['e2ee'],
            'tee':           chat_config['tee'],
            'edit_responses': chat_config['edit_responses'],
            'write_history':  chat_config['write_history'],
            'download_field': chat_config.get('download_field'),
            'history':       history,
            'start_message': start_message,
            'start_fname':   start_fname or '',
            'start_macro_name': start_macro_name,
            'initial_state':  chat_config.get('state'),
            'debug_state':    chat_config.get('debug', False),
            'temperature_override': temperature,
            'write_history_override': write_history,
            'debug_override': debug,
            'session_name':  session_name,
            'session_title': session_title,
            'initial_history_data': initial_history_data,
            'initial_history_summary': initial_history_summary,
            'history_mtime': history_mtime,
        }))

    def _stream(self, request):
        data         = json.loads(request.body)
        config_fname = data.get('config')
        history_fname = data.get('history')
        service_name  = data.get('service')
        provider_name = data.get('provider')
        model_name    = data.get('model')
        user_input    = data.get('message', '').strip()
        start_fname   = data.get('start') or None
        temperature   = data.get('temperature')
        write_history = data.get('write_history')
        debug         = data.get('debug')
        current_state = data.get('state')
        history_client_data = data.get('history_data') or []
        generated_start = bool(data.get('generated_start'))

        if not config_fname or not user_input:
            return StreamingHttpResponse(status=400)

        config_path = discover.resolve_path(config_fname)
        if not os.path.exists(config_path):
            return StreamingHttpResponse(status=404)

        if not history_fname:
            return StreamingHttpResponse(status=400)
        history_path = discover.resolve_history_path(history_fname)

        def generate():
            try:
                start_path = None
                if start_fname:
                    start_path = discover.resolve_start_file(config_path, start_fname)
                config = _load_persona_config(
                    config_path,
                    service_name,
                    provider=provider_name,
                    model=model_name,
                    history_path=history_path,
                    start_file_path=start_path,
                )
                _apply_chat_overrides(config, temperature=temperature,
                                      write_history=write_history, debug=debug)
                persona = Persona(config)
                if current_state is not None and persona.has_structured_output():
                    persona.set_state(current_state)

                # write_history=False: no file was created; seed history from client
                hp = persona.history_path
                if history_client_data and (hp is None or not os.path.exists(hp)):
                    persona._llm.overwrite_history(list(history_client_data))
                had_turns = any(True for _turn in persona.iterate_history())
                is_start_turn = False
                if generated_start and start_fname and not had_turns:
                    start_path = discover.resolve_start_file(config_path, start_fname)
                    is_start_turn = (
                        start_path is not None
                        and user_input == discover.start_file_body(start_path)
                    )

                last_chunk = None
                gen = persona.stream(user_input)
                for chunk in gen:
                    last_chunk = chunk
                    delta = chunk.get('content', '')
                    if delta:
                        yield f"data: {json.dumps({'chunk': delta})}\n\n"

                hp = persona.history_path
                done_data: dict = {
                    'done':          True,
                    'history_fname': os.path.basename(hp) if hp else history_fname,
                }
                error_info = (last_chunk or {}).get('error')
                if error_info:
                    error_message = error_info.get('message', str(error_info))
                    yield f"data: {json.dumps({'error': error_message})}\n\n"
                    return
                if is_start_turn:
                    persona.mark_start_turn_generated()
                done_data['e2ee'] = persona.is_e2ee()
                done_data['tee']  = persona.is_tee()
                if persona.has_structured_output():
                    done_data['state'] = persona.get_state()
                    done_data['debug'] = persona.show_state()
                    download = persona.download_payload()
                    if download is not None:
                        done_data['download'] = download
                try:
                    turns = list(persona.iterate_history())
                    if turns:
                        latest_turn = turns[-1]
                        done_data['turn_id'] = latest_turn.get('id')
                        done_data['turn'] = latest_turn
                    done_data['summary'] = persona.history_summary()
                    # write_history=False: return full history so client stays in sync
                    if hp is None or not os.path.exists(hp):
                        done_data['history_data'] = turns
                except Exception:
                    logger.exception('could not iterate history after stream')
                yield f"data: {json.dumps(done_data)}\n\n"

            except Exception as e:
                logger.error('stream error: %s', traceback.format_exc())
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        resp = StreamingHttpResponse(generate(), content_type='text/event-stream')
        resp['Cache-Control']    = 'no-cache'
        resp['X-Accel-Buffering'] = 'no'
        return resp

    def system(self, request):
        config_fname = request.GET.get('config', '')
        service_name = request.GET.get('service', '')
        provider_name = request.GET.get('provider', '')
        model_name = request.GET.get('model', '')
        manual_ready = _manual_selection_ready(provider_name, model_name)
        if not config_fname or (not service_name and not manual_ready):
            return redirect(self._u('index'))

        config_path = discover.resolve_path(config_fname)
        if not os.path.exists(config_path):
            return redirect(self._u('index'))

        display_name = Config.display_name(config_path)
        system_text  = None
        error        = None
        try:
            config = _load_persona_config(
                config_path,
                service_name,
                provider=provider_name if manual_ready else None,
                model=model_name if manual_ready else None,
            )
            persona = Persona(config)
            system_text = persona.current_system_text
        except ConfigError as e:
            error = str(e)
        except Exception as e:
            logger.exception('unexpected error building system view for %s', config_fname)
            error = 'An unexpected error occurred.'

        return render(request, self._t('system.html'), self._ctx(f'System — {display_name}', {
            'display_name': display_name,
            'config_fname': config_fname,
            'service_name': service_name,
            'provider_name': provider_name if manual_ready else '',
            'model_name': model_name if manual_ready else '',
            'system_text':  system_text,
            'error':        error,
        }))

    def service(self, request):
        fname = request.GET.get('file', '')
        if not fname or '/' in fname:
            return redirect(self._u('index'))

        svc_path = discover.resolve_path(fname)
        if not os.path.exists(svc_path):
            return redirect(self._u('index'))

        svc_id = fname[:-len('.service.json')] if fname.endswith('.service.json') else fname
        error  = None
        svc    = None
        try:
            svc = Service.from_file(svc_path)
        except ConfigError as e:
            error = str(e)
        except Exception as e:
            logger.exception('unexpected error loading service %s', fname)
            error = 'An unexpected error occurred.'

        title = svc.name if svc and svc.name else svc_id
        return render(request, self._t('service.html'), self._ctx(title, {
            'fname':  fname,
            'svc_id': svc_id,
            'svc':    svc,
            'error':  error,
        }))

    def services(self, request):
        service_list = [
            {
                'id': svid,
                'display': d,
                'fname': f,
                'features': discover.service_features_label(mdl, features),
                'provider': sup,
                'model': mdl,
                'invalid': discover.is_invalid_description_file(full),
                'error': discover.invalid_description_error(full) or '',
            }
            for svid, d, f, full, sup, mdl, features in discover.find_service_files()
        ]
        return render(request, self._t('services.html'),
                      self._ctx('Services', {'services': service_list}))

    def models(self, request):
        provider = request.GET.get('provider', '')
        if not provider:
            providers = discover.find_providers()
            return render(request, self._t('models.html'), self._ctx('Models', {
                'providers': providers,
                'provider':  None,
                'models':    None,
                'error':     None,
            }))

        model_list = []
        error      = None
        try:
            model_list = discover.list_models(provider)
        except Exception as e:
            logger.exception('could not list models for provider %s', provider)
            error = str(e)

        return render(request, self._t('models.html'), self._ctx(f'Models — {provider}', {
            'providers': None,
            'provider':  provider,
            'models':    model_list,
            'error':     error,
        }))

    def _render_markdown(self, request):
        data = json.loads(request.body)
        return JsonResponse({'html': _render_md(data.get('text', ''))})

    def _edit_history(self, request):
        data          = json.loads(request.body)
        history_fname = data.get('history', '')
        turn_id       = data.get('turn_id')
        content       = data.get('content', '')
        truncate      = data.get('truncate', False)
        role          = data.get('role', 'assistant')

        if not history_fname or not turn_id:
            return JsonResponse({'error': 'missing parameters'}, status=400)

        history_path = discover.resolve_history_path(history_fname)

        if not os.path.exists(history_path):
            # write_history=False: apply edit to client-provided in-memory history
            client_history = data.get('history_data') or []
            turns = list(client_history)
            if truncate:
                idx = next((i for i, t in enumerate(turns) if t.get('id') == turn_id), None)
                if idx is not None:
                    turns = turns[:idx]
            else:
                for t in turns:
                    if t.get('id') == turn_id:
                        if role == 'user':
                            t['user']['content'] = content
                        else:
                            t['assistant']['content'] = content
                        break
            return JsonResponse({'ok': True, 'history_data': turns})

        try:
            session = discover.build_session_for_history(history_path)
            if truncate:
                session.truncate_history(turn_id)
            else:
                kwargs = {}
                if role == 'user':
                    kwargs['user_content'] = content
                else:
                    kwargs['assistant_content'] = content
                session.update_history_item(turn_id, **kwargs)
            session.shutdown()
            return JsonResponse({'ok': True})
        except Exception as e:
            logger.exception('could not edit history %s', history_fname)
            return JsonResponse({'error': 'An unexpected error occurred.'}, status=400)

    def _delete_history(self, request):
        data = json.loads(request.body)
        history_fname = data.get('history', '')
        if not history_fname or '/' in history_fname or not history_fname.endswith('.jsonl'):
            return JsonResponse({'error': 'invalid filename'}, status=400)
        history_path = discover.resolve_history_path(history_fname)
        ok, msg = discover.delete_history_file(history_path)
        if ok:
            return JsonResponse({'ok': True})
        return JsonResponse({'error': msg}, status=400)

    def _set_history_name(self, request):
        data = json.loads(request.body)
        history_fname = data.get('history', '')
        name = data.get('name', '')
        if not history_fname or '/' in history_fname or not history_fname.endswith('.jsonl'):
            return JsonResponse({'error': 'invalid filename'}, status=400)
        history_path = discover.resolve_history_path(history_fname)
        if not os.path.exists(history_path):
            return JsonResponse({'error': 'history file not found'}, status=404)
        try:
            session = discover.build_session_for_history(history_path)
            session.set_history_name(name)
            session.shutdown()
            return JsonResponse({'ok': True})
        except Exception:
            logger.exception('could not set session name for %s', history_fname)
            return JsonResponse({'error': 'An unexpected error occurred.'}, status=400)

    def _set_history_title(self, request):
        data = json.loads(request.body)
        history_fname = data.get('history', '')
        title = data.get('title', '')
        if not history_fname or '/' in history_fname or not history_fname.endswith('.jsonl'):
            return JsonResponse({'error': 'invalid filename'}, status=400)
        history_path = discover.resolve_history_path(history_fname)
        if not os.path.exists(history_path):
            return JsonResponse({'error': 'history file not found'}, status=404)
        try:
            session = discover.build_session_for_history(history_path)
            session.set_history_title(title)
            session.shutdown()
            return JsonResponse({'ok': True})
        except Exception:
            logger.exception('could not set session title for %s', history_fname)
            return JsonResponse({'error': 'An unexpected error occurred.'}, status=400)

"""llemon_djview.persona_host - host-selection helpers for Grove's persona flow.

Backing for Grove's optional host selection (see this repository's
upgrade.md, Task 11): validates a request host against the configured
allowlist, resolves the effective provider/model for a request, and
derives a URL override through the persona layer's discover module.
`llemon_djview` must not import `hty7.sshtunnel` directly (see hty7's own
AGENTS.md "Layer isolation"); `discover.resolve_host_endpoint()` and its
`TunnelError` re-export are the only door into that mechanism.
"""

import logging

from hty7.llemon.persona import discover
from hty7.llemon.persona.config import ConfigError

logger = logging.getLogger(__name__)


def validate_host_selection(persona_hosts: tuple[str, ...], host: str, provider: str) -> None:
    """Raise `ConfigError` when `host` is set but invalid for `provider`.

    A request error: no network activity, no tunnel opened. An empty
    `host` is always valid — it means no selection. Otherwise `host` must
    exactly match one configured entry, and `provider` must be one a
    hosted connection supports (`ollama` or `llama.cpp`, checked via
    `discover.validate_host_provider()`). Either failure means the
    request is stale or forged, never a runtime condition — callers doing
    an ordinary GET-driven render should catch this and clear the
    selection silently rather than surface it as an error; only a POST
    revalidation (`_stream`) should let it become a displayed rejection.
    """
    if not host:
        return
    if host not in persona_hosts:
        raise ConfigError(f'Host {host!r} is not configured')
    discover.validate_host_provider(provider)


def resolve_effective_provider_and_model(
    config_path: str, service_name: str, manual_provider: str, manual_model: str,
) -> tuple[str, str]:
    """Return the `(provider, model)` actually in effect for a request.

    Manual selection wins when both a provider and a model are given —
    mirrors `_manual_selection_ready()` elsewhere in this package.
    Otherwise, when a service is picked, its provider/model come from
    `discover.get_services(config_path)` (which never raises — an
    invalid config or service file yields `[]` or an "Invalid: ..."-titled
    row rather than an exception, so no `try`/`except` is needed here).
    Returns `('', '')` when neither a complete manual selection nor a
    matching service applies.
    """
    if manual_provider and manual_model:
        return manual_provider, manual_model
    if service_name:
        for svc_name, _svc_display, svc_provider, svc_model, *_rest in (
            discover.get_services(config_path)
        ):
            if svc_name == service_name:
                return svc_provider or '', svc_model or ''
    return '', ''


def host_url_override(provider: str, host: str) -> str:
    """Resolve `host`'s endpoint for `provider` through the persona layer.

    Normalizes a tunnel failure to a fixed, brief message — the
    underlying `sshtunnel.TunnelError` (which may include the jump-host
    hint and other SSH diagnostics) is logged with a full traceback and
    never reaches the caller. The message omits the `Error:` display
    prefix, matching every other `ConfigError` this package's views
    render as `{{ error }}` with no template-added prefix (Grove's
    stream/JS error path adds its own `Error:` client-side instead — see
    `chat.html`; embedding the prefix here would double it there while
    only being needed for the plain server-rendered path). A
    `ConfigError` from an unsupported provider (`resolve_host_endpoint()`'s
    own check) propagates unchanged.
    """
    try:
        return discover.resolve_host_endpoint(provider, host)
    except discover.TunnelError:
        logger.exception(
            'could not open a tunnel to host %r for provider %r', host, provider,
        )
        raise ConfigError(f'SSH connection to {host} could not be created.') from None

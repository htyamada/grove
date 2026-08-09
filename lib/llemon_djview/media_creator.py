"""Provider-neutral presentation contracts shared by media creators."""

from copy import deepcopy
from typing import Any


def build_operation_presentation(
    operation: str,
    *,
    model_options: list[dict[str, Any]] | None = None,
    selected_model: str | None = None,
    default_model: str | None = None,
    defaults: dict[str, Any] | None = None,
    controls: dict[str, Any] | None = None,
    availability: dict[str, Any] | None = None,
    selected_target: dict[str, Any] | None = None,
    notes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build presentation data for one modality-specific operation.

    ``selected_model`` and ``default_model`` are separate on purpose: current
    creators may select a default for initial display, while future providers
    may require an explicit selection without defining a backend default.
    Model-less operations, such as image upscaling, leave both values as
    ``None``.
    """
    if not operation:
        raise ValueError('operation is required')
    return {
        'operation': operation,
        'model_options': deepcopy(model_options or []),
        'selected_model': selected_model,
        'default_model': default_model,
        'defaults': deepcopy(defaults or {}),
        'controls': deepcopy(controls or {}),
        'availability': deepcopy(availability or {}),
        'selected_target': deepcopy(selected_target),
        'notes': deepcopy(notes or {}),
    }


def build_model_target(
    provider: str,
    api: str,
    operation: str,
    model: str,
    *,
    controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one cacheable selected-model presentation target."""
    if not provider or not api or not operation or not model:
        raise ValueError('provider, api, operation, and model are required')
    identity = {
        'provider': provider,
        'api': api,
        'operation': operation,
        'model': model,
    }
    return {
        'target': identity,
        'controls': deepcopy(controls or {}),
    }


def build_creator_presentation(
    provider: str,
    api: str,
    operations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the stable outer presentation shape for one media provider/API."""
    if not provider:
        raise ValueError('provider is required')
    if not api:
        raise ValueError('api is required')
    return {
        'provider': provider,
        'api': api,
        'target': {
            'provider': provider,
            'api': api,
            'operation': 'provider',
            'model': None,
        },
        'operations': deepcopy(operations),
    }

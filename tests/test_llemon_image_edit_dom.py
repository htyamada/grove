"""Runtime (real-DOM) regression test for llemon_image/image.html's
multi-image edit UI (Task 13 Phase 2).

The rest of this repo's JS coverage is render-only: tests/
test_image_creator_render.py renders the template and string-matches the
output, and node --check validates only syntax. Neither can catch a
runtime ReferenceError, a stale closure variable, or an event handler
that silently never fires -- and this series of reviews found several of
exactly those defects, each invisible to that coverage and only caught by
actually executing the page. This file commits that execution as a
regression test instead of leaving it as an ad hoc, uncommitted check:
it renders the template against a fixture edit_meta (below) covering
every scenario, then hands the HTML to tests/js/edit_images_dom_test.js,
a jsdom harness that drives it end to end.

Requires Node and this repo's own JS dependency, installed with
`npm install` inside tests/js/ (a package.json/package-lock.json are
committed there; node_modules/ is gitignored, matching every other
Node project). Skipped -- not failed -- when either is unavailable, so a
checkout that has not run `npm install` there loses only this file's
coverage rather than failing the whole suite.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import test_image_creator_render as _render_mod  # noqa: E402

_JS_DIR = _TESTS_DIR / 'js'
_HARNESS = _JS_DIR / 'edit_images_dom_test.js'


def _node_available() -> bool:
    return shutil.which('node') is not None


def _jsdom_installed() -> bool:
    if not _node_available():
        return False
    result = subprocess.run(
        ['node', '-e', "require.resolve('jsdom')"],
        cwd=_JS_DIR, capture_output=True,
    )
    return result.returncode == 0


def _django_available() -> bool:
    return getattr(_render_mod, 'settings', None) is not None


_PICKER_ITEMS = [
    {'fname': 'cat.png', 'url': '/img/cat.png', 'thumb_url': '/thumb/cat.png'},
    {'fname': 'dog.png', 'url': '/img/dog.png', 'thumb_url': '/thumb/dog.png'},
    {'fname': 'bird.png', 'url': '/img/bird.png', 'thumb_url': '/thumb/bird.png'},
    {'fname': 'fox.png', 'url': '/img/fox.png', 'thumb_url': '/thumb/fox.png'},
]


def _role(name, required, *, accepted=(), required_transport=None, available=()):
    return {
        'name': name, 'required': required, 'position': 0,
        'description': None, 'aliases': [],
        'accepted_source_kinds': list(accepted),
        'required_backend_transports': (
            {'data_url': required_transport} if required_transport else {}
        ),
        'available_backend_transports': list(available),
    }


def _make_option(
    model_id, *, shape='ordered', effective_max_count=1, min_count=None,
    roles=None, transport_warnings=None,
):
    opt = _render_mod._presentation(model_id, generate=False, edit=True)
    roles = roles or []
    opt['edit_inputs'].update({
        'shape': shape,
        'min_count': min_count if min_count is not None else (len(roles) or 1),
        'max_count': (len(roles) or None) if shape == 'named' else None,
        'effective_max_count': effective_max_count,
        'roles': roles,
        # Named-schema fixtures deliberately leave the top-level fields
        # empty/disjoint from any single role's own facts -- exercising
        # the same "cross-role intersection can be empty" case the
        # eligibility fix (Task 13 Phase 2 follow-up) covers.
        'accepted_source_kinds': [] if shape == 'named' else ['data_url'],
        'required_backend_transports': {},
        'available_backend_transports': [],
        'transport_warnings': transport_warnings or {},
    })
    return {'id': model_id, 'name': model_id, 'presentation': opt, 'display': model_id}


def _build_edit_meta():
    models = [
        _make_option('ordered-multi', shape='ordered', effective_max_count=3),
        _make_option('ordered-min2', shape='ordered', effective_max_count=2, min_count=2),
        _make_option(
            'named-roles', shape='named', effective_max_count=2,
            roles=[
                _role('first', True, accepted=['data_url']),
                _role('second', True, accepted=['data_url']),
            ],
        ),
        _make_option(
            # min_count (2) equals the required-role count, so satisfying
            # it with distinct roles and no duplicates always covers every
            # required role by pigeonhole -- "missing required role"
            # distinct from "too few images" needs a mixed required/
            # optional schema instead (assigning the optional role leaves
            # a required one unfilled while still meeting min_count).
            'mixed-required-optional', shape='named', effective_max_count=3,
            min_count=2,
            roles=[
                _role('req1', True, accepted=['data_url']),
                _role('req2', True, accepted=['data_url']),
                _role('opt1', False, accepted=['data_url']),
            ],
        ),
        _make_option(
            # Top-level accepted_source_kinds/required_backend_transports
            # are both empty (no fact common to both roles), but each role
            # independently accepts data_url via its own path.
            'disjoint-named-roles', shape='named', effective_max_count=2,
            roles=[
                _role('first', True, accepted=['data_url']),
                _role('second', True, required_transport='provider_upload',
                      available=['provider_upload']),
            ],
        ),
        _make_option(
            'mixed-optional-roles', shape='named', effective_max_count=2, min_count=0,
            roles=[
                _role('warned', False, required_transport='provider_upload',
                      available=['provider_upload']),
                _role('clean', False, accepted=['data_url']),
                _role('unreachable', False),
            ],
            transport_warnings={'provider_upload': 'uploads leave LLemon-managed storage'},
        ),
        _make_option(
            'compat-a', shape='named', effective_max_count=2, min_count=0,
            roles=[
                _role('x', False, accepted=['data_url']),
                _role('y', False, accepted=['data_url']),
            ],
        ),
        _make_option(
            # Identical shape/effective_max_count/min_count/role names to
            # compat-a, but role 'x' now needs warning consent -- a role
            # gaining or losing a warning is *not* a usability change (the
            # role is equally selectable either way), so the compatibility
            # signature must treat this as the same schema and preserve a
            # stale selection rather than clearing it.
            'compat-b', shape='named', effective_max_count=2, min_count=0,
            roles=[
                _role('x', False, required_transport='provider_upload',
                      available=['provider_upload']),
                _role('y', False, accepted=['data_url']),
            ],
            transport_warnings={'provider_upload': 'uploads leave LLemon-managed storage'},
        ),
        _make_option(
            # Same shape/effective_max_count/min_count/role names as
            # compat-a, but role 'x' has genuinely lost its only usable
            # path (its required transport is no longer available) -- a
            # real usability change, unlike compat-b's warning-only one,
            # so the compatibility signature must still detect this as a
            # different schema and clear a stale selection.
            'compat-c', shape='named', effective_max_count=2, min_count=0,
            roles=[
                _role('x', False, required_transport='provider_upload', available=[]),
                _role('y', False, accepted=['data_url']),
            ],
        ),
    ]
    return {
        'supports_edit': True,
        'edit_models': [m['id'] for m in models],
        'edit_model_options': models,
        'selected_edit_model': 'ordered-multi',
        'default_edit_model': 'ordered-multi',
        'edit_aspect_ratios': ['1:1', '16:9'],
        'default_edit_aspect_ratio': '1:1',
        'edit_image_sizes': [],
        'default_edit_image_size': '',
    }


def _render_page() -> str:
    tc = _render_mod.ImageCreatorRenderTests()
    tc.setUp()
    view = tc.view
    request = tc.factory.get('/')
    overrides = {
        'normalize_provider_api': lambda p=None, a=None: ('venice', 'generation'),
        'list_image_models_with_metadata': mock.Mock(
            return_value=[{'id': 'm1', 'name': 'Model One', 'description': 'desc',
                           'presentation': _render_mod._presentation('m1')}]),
        'model_quirk_labels': mock.Mock(return_value=[]),
        'default_system_prompt': mock.Mock(return_value=None),
        'model_presentation': mock.Mock(
            side_effect=lambda m, *a, **k: _render_mod._presentation(m)),
        'get_model_tag_states': mock.Mock(return_value={}),
        'get_notes_load_errors': mock.Mock(return_value=[]),
        'get_tags': mock.Mock(return_value=[]),
        'get_reverse_tags': mock.Mock(return_value=[]),
        'get_notes_slot': mock.Mock(return_value=''),
        'aspect_ratios': mock.Mock(return_value=['1:1']),
        'image_sizes': mock.Mock(return_value=['1K']),
        'default_aspect_ratio': mock.Mock(return_value='1:1'),
        'default_image_size': mock.Mock(return_value='1K'),
        'default_model_for_presentation': mock.Mock(return_value='m1'),
        '_provider_config': mock.Mock(return_value=_render_mod._PROVIDER_CONFIG),
        'PROVIDERS': ['venice', 'openrouter'],
        'supports_edit': mock.Mock(return_value=True),
        'supports_upscale': mock.Mock(return_value=True),
        '_edit_metadata': mock.Mock(return_value=_build_edit_meta()),
    }
    with _render_mod.override_settings(**_render_mod._DJANGO_TEST_OVERRIDES):
        with mock.patch.dict(view.image_creator.__globals__, overrides):
            with mock.patch.object(view, '_gallery_picker_items', return_value=_PICKER_ITEMS):
                response = view.image_creator(request)
    assert response.status_code == 200, response.status_code
    return response.content.decode('utf-8')


@unittest.skipUnless(_django_available(), 'django is not installed')
@unittest.skipUnless(
    _jsdom_installed(),
    'node/jsdom not available under tests/js/ -- run `npm install` there to enable '
    '(see tests/js/package.json)',
)
class EditImagesDomTests(unittest.TestCase):
    def test_multi_image_edit_ui_runtime_behavior(self) -> None:
        html = _render_page()
        with tempfile.NamedTemporaryFile(
            'w', suffix='.html', delete=False, encoding='utf-8',
        ) as f:
            f.write(html)
            html_path = f.name
        try:
            result = subprocess.run(
                ['node', str(_HARNESS), html_path],
                cwd=_JS_DIR, capture_output=True, text=True, timeout=60,
            )
        finally:
            Path(html_path).unlink(missing_ok=True)
        if result.returncode != 0:
            self.fail(
                'jsdom harness reported a failure (see tests/js/edit_images_dom_test.js):\n'
                + result.stdout + '\n' + result.stderr
            )


if __name__ == '__main__':
    unittest.main()

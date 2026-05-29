# Repository Guidelines

## Project Structure & Module Organization

This repository contains the `llime` Django project and shared local libraries.
Application code for the web project lives under `llime/`: project settings are
in `llime/config/`, Django apps are in directories such as `base/`, `llemon/`,
and `to_do_list/`. Project specs are consolidated in root `specs/`, including
To Do, mediaview, and LLemon frontend/media specs. Reusable shared code lives
under `lib/`; currently `lib/mediaview/` is a standalone Django app, and
`lib/llemon_djview/` contains the shared LLemon Django views and templates. The
active consumers of `lib/mediaview` and `lib/llemon_djview` are `llime` and
`../qat/knip`.

Templates are stored per app in `templates/`, and static assets are under each
app’s `static/` directory. Tests, where present, use each app’s `tests.py`.

## Build, Test, and Development Commands

- `cd llime && ./manage.py check` validates Django settings, app loading, URL
  configuration, and template discovery.
- `cd llime && ./start-server` starts the local Django development server.
- `python3 -m py_compile lib/mediaview/*.py lib/llemon_djview/*.py` checks the
  shared Django packages for Python syntax errors.
- `cd llime && ./manage.py test` runs Django tests for apps that define them.

## Coding Style & Naming Conventions

Use Python 3 with 4-space indentation. Keep Django views and helpers small,
module-local, and named descriptively; private helpers should use a leading
underscore, for example `_resolve()` or `_associated_sidecars()`. Prefer
`pathlib.Path` for filesystem work. Templates should use plain Django template
syntax and plain browser JavaScript unless an existing app already uses another
pattern.

## Testing Guidelines

Run `./manage.py check` after settings, URL, template, or app-loading changes.
Run focused Django tests with `./manage.py test app_name` when changing app
behavior. For `mediaview`, also run the `py_compile` command above and manually
verify browse, thumbnail, metadata, move, delete, and sidecar handling when those
flows are touched.

## Commit & Pull Request Guidelines

Git history uses short, direct summaries such as `Moved llime from hty7 to
grove`. Keep commits focused and use concise past-tense or imperative messages.
Pull requests should state the user-visible change, list verification commands
run, note config or deployment impacts, and include screenshots for UI changes.

## Security & Configuration Tips

Do not commit secrets or machine-local config. Runtime settings come from files
under `~/etc/`, while generated logs and caches live under `~/var/`. Shared apps
may be imported by host projects through `sys.path`, so keep public module names
stable and document path changes in both host settings and `lib/*` docs.

# Repository Guidelines

## Project Structure & Module Organization

`mediaview` is a reusable Django app for browsing filesystem media trees. Core
view logic lives in `views.py`; URL routes are in `urls.py`; thumbnail creation
is in `thumbs.py`; app configuration loading is in `conf.py`; Django app
metadata is in `apps.py`. The browser UI is a single template at
`templates/mediaview/browse.html`. Design and behavior notes live in
`../../specs/mediaview-architecture.md`. There is no local test package in this
directory; integration happens through host Django projects such as `../../llime`.

## Build, Test, and Development Commands

- `python3 -m py_compile views.py thumbs.py conf.py urls.py apps.py` checks
  local Python syntax without starting Django.
- From `../../llime`, `./start-server` runs the development server for manual
  browser testing when this app is installed there.
- From `../../llime`, `python3 manage.py check` validates Django settings, URL
  configuration, and app loading.
- From the repository root, `make` initializes the shared git submodule and
  installs unrelated PHP support files.

## Coding Style & Naming Conventions

Use Python 3 with 4-space indentation and small module-level helper functions.
Private helpers use leading underscores, for example `_resolve()` and
`_associated_sidecars()`. Keep path handling based on `pathlib.Path`, and
validate user-provided paths with `.resolve()` plus `relative_to(root)` before
filesystem access. Template JavaScript is plain browser JavaScript; keep it
close to the UI behavior it supports.

## Testing Guidelines

No dedicated automated tests currently ship with this app. For code changes,
at minimum run the syntax check above and `python3 manage.py check` from a host
project. For UI or file-operation changes, manually verify browse, thumbnail,
delete, mkdir, and drag-and-drop move flows. Include sidecar cases such as
`photo.jpg.json`, `photo.jpg.xmp`, `photo.xmp`, and `photo.aae` when touching
metadata or move/delete behavior.

## Commit & Pull Request Guidelines

Recent commit messages are short, imperative or past-tense summaries such as
`Added a mkdir button.` or `Made specs presentation uniform.` Keep new commits
similarly concise and focused. Pull requests should describe the user-visible
change, list manual verification performed, and include screenshots or screen
recordings for UI changes. Link related issues or notes when available.

## Security & Configuration Tips

Runtime roots and cache paths come from `~/etc/mediaview.conf`, selected by the
host project’s `MEDIAVIEW_LABEL`. Never trust request paths directly, never
allow cross-root moves, and avoid exposing dotfiles or non-media files in the
listing.

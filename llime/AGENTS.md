# llime — Django LLM/Image Web App

Django project served at `/zorf/llime/` via gunicorn.
Python venv: `~/opt/web` (created by `~/src/hty7/set-up-system/250-python-web.sh`).

```
./start-server          # runs manage.py runserver
```

## Apps

| App | URL prefix | Purpose |
|-----|-----------|---------|
| `base` | `/` | Home page |
| `LLemon` | `/llemon/` | LLM chat interface with a unified Media app → see [llemon/CLAUDE.md](llemon/CLAUDE.md) |
| `Image Handler` | `/image_handler/` | Image gallery viewer → see [image_handler/CLAUDE.md](image_handler/CLAUDE.md) |
| `To Do list` | `/to-do-list/` | Standalone task app → see [to-do-list/CLAUDE.md](to-do-list/CLAUDE.md), [../specs/todo-spec.md](../specs/todo-spec.md) and [../specs/todo-impl.md](../specs/todo-impl.md) |

### LLemon Media App

LLemon exposes a single Media app at `/llemon/media/`. Image Creator and
Video Creator are separate pages within that app, while gallery, archive, and
uploads are shared. Media pages automatically detect file type and apply
appropriate tools:

- **Gallery**: Browse all media (images and videos) with lazy-loaded thumbnails, categories, and full-screen viewers
- **Archive**: Move media out of active gallery
- **Uploads**: User-uploaded media with browsing interface
- **Image Creator**: Generate, upscale, or edit images
- **Video Creator**: Generate videos with text-to-video APIs

Media-type detection is automatic: operations route to appropriate tools
inside the Media app (e.g., video media reloads in Video Creator, image media
reloads in Image Creator).

## Key settings (`config/settings.py`)

- `FORCE_SCRIPT_NAME = '/zorf/llime'` — all `reverse()` calls include this prefix
- Calls `discover.init()` at startup; paths come from `~/etc/llm.conf`

## Libraries used (installed, not editable)

- `hty7.llemon` — LLM config/service layer; source in `~/src/hty7/python3/lib/hty7/llemon/`
- `hty7.imhandler` — image scanning, thumbnailing; source in `~/src/hty7/python3/lib/hty7/imhandler/`

**IMPORTANT:** Always edit files in the source tree (`~/src/hty7/python3/lib/hty7/`), never the installed version in site-packages. Changes to either library must be applied to the source tree and the user will reinstall the libraries and restart.

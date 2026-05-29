# Persona Frontend Access Patterns

This document describes how each frontend accesses the `hty7.llemon`
package: `llemon-tui`, `llemon-cli`, and the Django view set backed by
`llemon_djview`.

---

## 1. Package Overview

The relevant public surface of `hty7.llemon.persona`:

| Module | Role |
|--------|------|
| `discover` | Initialisation; file discovery; path resolution; history I/O helpers; session builder |
| `Config` | Loads and resolves a single `.cfg.json` into a system prompt |
| `Session` | Holds the user's type/config/service/history selection; builds a `Persona` |
| `Persona` | Wraps the chosen LLM backend; streams responses; saves history |
| `Service` | Parses a `.service.json` file |

The relevant public surface of `hty7.llemon.core`:

| Symbol | Role |
|--------|------|
| `init(appconfig)` | Read `[*.llemon.core]`; set history/log defaults |
| `configure(*, history_dir, log_dir)` | Override core values; called by `discover.init()` |
| `get_history_dir()` / `get_log_dir()` | Return effective values after all overrides |

The relevant public surface of `hty7.curses_view` used by `llemon-tui`:

| Symbol | Role |
|--------|------|
| `CursesChatView` | Generic curses chat display; persona adaptation is supplied by callbacks |
| `CursesCommandView` | Generic curses transcript plus command-entry view |

`hty7.curses_view` is a separate reusable package, documented by its own
`spec.md` and `impl.md`. It may be extended for additional generic curses
views; persona/session adaptation remains in `hty7.llemon.cli`.

The relevant public surface of `hty7.llemon.cli`:

| Symbol | Role |
|--------|------|
| `init(data_dir, appconfig)` | Initialise core and persona; call once at startup |
| `main(data_dir, appconfig)` | Full entry point: init + run the CLI REPL |
| `run_cli_app(data_dir, conf_path)` | Executable entry point for raw CLI |
| `run_tui_app(data_dir, conf_path)` | Executable entry point for curses TUI |

---

## 2. Initialisation

### 2.1 Terminal frontends (`llemon-tui`, `llemon-cli`)

Driver scripts set three path variables at the top of the file:

```python
_here     = os.path.dirname(os.path.abspath(__file__))
_data_dir = os.path.join(_here, 'persona')
_conf     = os.path.join(_here, 'llemon.conf')
_variant  = 'qat' if '-q' in sys.argv else 'hty7'
```

`make install` rewrites `_data_dir` and `_conf` with `sed` so that
installed scripts point at the installed persona directory and the merged
`~/etc/hty7.conf` respectively.

The terminal launchers now delegate argument parsing and AppConfig
construction to `hty7.llemon.cli`:

**`llemon-tui`:**
```python
from hty7.llemon.cli import run_tui_app

run_tui_app(_data_dir, _conf)
```

**`llemon-cli`:**
```python
from hty7.llemon.cli import run_cli_app

run_cli_app(_data_dir, _conf)

_appconfig = AppConfig(_conf, _variant)
main(_data_dir, _appconfig)
```

`cli.init()` (called by the executable entry points) delegates to
`discover.init(data_dir, appconfig)`, which:

1. Calls `core.init(appconfig)` to populate `[*.llemon.core]` defaults.
2. Reads `[*.llemon.persona]` and overrides core values via
   `core.configure()` where the persona section specifies them.
3. Sets `discover.description_dirs`, `discover.history_dir`, and
   `discover.log_dir` from the resolved values.
4. Applies dev-mode detection (see `llemon-conf-spec.md` §5).

`discover.init_logging()` then opens a timestamped trace log in
`discover.log_dir` (if set).

### 2.2 Django (`LLemonViewSet`)

The deployed Django front ends include `llemon_djview.persona_urls` at
`/llemon/persona/`. The shared `llemon_djview.views` module instantiates
`LLemonViewSet('llemon_persona', 'llemon_persona', base_nav=..., nav=...)`.
Host projects should configure the shared app and URL modules instead of
carrying local persona view/URL wrappers.

| Parameter | Purpose |
|-----------|---------|
| `base_nav` | Left-side navbar items (list of `{'name': str, 'url': str}` dicts) |
| `nav` | Right-side navbar items prepended before the section-specific links on every page; optional |

The section-specific right-side links (type entries, Models, Services) are
appended after any items supplied via `nav`.

`LLemonViewSet` does not call `discover.init()`. The host Django project
loads LLemon settings at application startup through
`llemon_djview.django_settings(<variant>)`, which initializes the configured
variant and returns the Django settings required by the shared views.

All `discover.*` discovery calls within `LLemonViewSet` use the standard
discovery functions with no special arguments. CWD is never added to the
search path implicitly; it is only searched if `"."` appears in the
configured `description_dirs`.

### 2.3 Conversation Context Usage Display

Both `llemon-tui` and the Django persona chat page expose a lightweight
"conversation size versus context window" estimate derived from the most
recent turn's `usage.prompt_tokens` and model id.

The intended behavior is:

- if the latest turn has no `usage.prompt_tokens`, show no context estimate;
- if `prompt_tokens` exists and the model matches a known context-window
  mapping, show a percentage plus the estimate, formatting prompt and context
  sizes with consistent compact units;
- if `prompt_tokens` exists but the model is unknown, fall back to a raw token
  count only.

The current frontend model-to-context mapping is heuristic and substring-based:

| Pattern | Context window |
|---------|----------------|
| `claude` | 200,000 |
| `gpt-4o` | 128,000 |
| `gpt-4-turbo` | 128,000 |
| `gpt-4` | 8,192 |
| `gpt-3.5` | 16,384 |
| `gemini-2` | 1,000,000 |
| `gemini-1.5` | 1,000,000 |
| `gemini` | 32,768 |
| `llama-3` | 8,192 |
| `mistral` | 32,768 |

The estimate is generated in the core layer and carried with the same turn data
path as the conversation itself, rather than recalculated independently in each
frontend.

When best-effort model capability metadata includes token pricing, the core
also attaches per-turn cost estimates and provides a conversation summary with
the current token rates, shown as dollars per million tokens, plus the
accumulated total estimated cost for the session so far.

The two frontends format the same estimate differently:

- `llemon-tui` shows two right-gutter lines: `ctx NN%` and
  `est PROMPT/CONTEXT`.
- the Django page shows one compact label near the session name:
  `ctx NN% (PROMPT/CONTEXT)`.

The threshold coloring is shared semantically:

- at or above 75% of the estimated context window: red + bold;
- at or above 60% and below 75%: yellow + bold;
- otherwise: dim/neutral.

The Django path must not rely on page-level startup selections alone for this
display. The working data path is:

1. the chat page render loads raw history turns from the `.jsonl` history file;
2. the backend derives `initial_usage` and `initial_history_model` from the
   most recent turn and injects them into the template;
3. the template initializes the counter from those explicit fields, falling
   back to scanning `initial_history_data` only if `initial_usage` is absent;
4. each stream completion event returns the latest `usage` and `model` from
   the final history turn so the page can refresh the counter after a reply.

For saved-history sessions, the browser must preserve `currentHistory` unless
the backend explicitly sends `history_data`. The normal saved-history stream
path may omit `history_data`, so replacing the client state with an absent
field is incorrect.

This behavior is currently implemented across these modified files:

- `lib/llemon_djview/__init__.py`
  - loads raw initial turn data from history files;
  - derives `initial_usage` and `initial_history_model`;
  - resolves effective provider/model for service-based chat launches;
  - returns the latest `usage` and `model` in stream completion events.
- `lib/llemon_djview/templates/llemon_persona/chat.html`
  - adds the visible token/context counter element;
  - initializes it from explicit server-provided usage/model data;
  - updates it after streaming responses;
  - avoids clobbering `currentHistory` when `history_data` is omitted.
- `python3/lib/hty7/llemon/tui/tui.py`
  - computes the same estimate from the latest persona history turn;
  - provides formatted state-gutter lines to the reusable curses view.
- `python3/lib/hty7/curses_view/chat.py`
  - generalizes right-gutter state lines from plain strings to
    `(text, curses_attr)` tuples;
  - renders per-line attributes and defines the yellow color pair used by the
    context warning state.
- `python3/prj/llemon/test/test_cli_status.py`
  - adds focused unit coverage for compact context-size formatting and the TUI
    token-provider output.

---

## 3. Config Selection

### `llemon-tui` — curses setup screen

`llemon-tui` uses the shared `.llemon.cli` setup engine. The curses setup
screen displays setup command output and collects slash commands, but config
selection itself is handled by `Session.apply_setup_command()`.

For play mode, `llemon-tui` may pre-populate setup from a macro file. The
frontend passes the macro path to `Session.configure_from_macro_file()`. If
the macro contained `start`, it enters `CursesChatView` with the resulting
`Persona`; otherwise it shows the setup screen with the selected parameters.
Macro-file resolution is persona-layer behavior; frontends must not duplicate
config, history, or start-file lookup logic.

### Session restart metadata

Frontends should save enough resolved session information in the history
directory to restart a session without repeating the menu flow. This is
frontend-owned metadata, not core history behavior.

The restart record should include:

- frontend instance ID;
- persona type ID;
- config file or resolved config name;
- service id;
- history file path;
- start file path or `null`;
- mode name and mode-specific launch files, such as play file, project file,
  or resource file;
- optional user-facing session name.

The preferred storage location is the history header `metadata` dict because it
travels with the transcript and is already persisted by the core. A frontend
may also maintain an index or sidecar file in `history_dir` for faster lookup or
hierarchical browsing, but the transcript remains the durable record.

Restart semantics are explicit:

- Selecting an existing history entry resumes that transcript and may use the
  restart metadata to restore the same config, service, start-file label, and
  mode-specific files.
- Selecting `new` or omitting history creates a fresh transcript and must not
  load prior turns, even if the same config, play file, project file, or
  resource file was used before.
- Start files are injected only into fresh sessions. They are not re-injected
  when resuming existing history.

### Session ownership

Only one process or browser session is expected to run a saved session at a
time. Frontends should enforce this with a session ownership record stored in
the history header metadata or a sidecar/index file in `history_dir`.

When a frontend starts or resumes a saved session, it generates a unique
`instance_id` such as a UUID. It records:

- `instance_id`;
- process ID or browser session ID when available;
- hostname or frontend name when available;
- acquisition timestamp;
- last heartbeat timestamp;
- optional expiration interval.

Before writing transcript, restart metadata, or mode-specific state, the
frontend must verify that the stored owner is either absent, expired, or
matches its own `instance_id`. If a different live owner is present, the save
or resume operation must fail with a concise user-facing error. A frontend may
offer an explicit "take over session" action that replaces an expired or
user-confirmed stale owner.

`llemon-tui` must release ownership when the user exits the program, returns to
selection and starts a different session, or explicitly closes the current
session. Releasing ownership clears the owner only when it still matches the
current `instance_id`; it must not clear a newer owner written by another
process.

`llemon-tui` should provide an explicit take-session action for cases where the
user knows a previous owner is stale or intentionally wants to move the session
to the current process. Taking a session replaces the saved owner with the
current process's `instance_id` and records the takeover time.

A running frontend must check ownership before accepting input that will
continue the session, before sending a model request, and before writing
history or mode-specific state. If the session has been taken by another
process, the previous owner must stop the session and report that ownership was
lost. It must not continue generating, saving, or mutating state.

Ownership is advisory single-writer protection, not a security boundary. It is
intended to prevent accidental corruption from two terminals, two browser tabs,
or a terminal and Django view writing the same session at once.

### `llemon-cli` — `Session` object

`llemon-cli` builds a `Session` and drives it through a line-oriented
command REPL (`[setup]` prompt):

```python
self.session = Session()
```

The user selects parameters via commands (`set type`, `set config`,
`set service`, `/init`) plus optional startup-only provider/model command-line
flags. `Session` stores the current selection
and exposes listing helpers delegating to `discover`:

| `Session` method | Delegates to |
|-----------------|-------------|
| `list_types()` | `discover.find_types()` |
| `list_configs()` | `discover.find_configs(type_filter=...)` |
| `list_services()` | `discover.get_services()` |
| `list_providers()` | `discover.find_providers()` |
| `list_models()` | `discover.list_models()` |
| `list_history()` | `discover.list_history_files()` + `discover.history_preview()` |
| `list_start_files()` | `discover.find_start_files()` + `discover.start_file_display()` |

When the user types `start`, `llemon-cli` calls
`self.session.build()` which calls `discover.build_config()` and
constructs a `Persona`.

### Django — `Session` per request

The Django setup UI is a browser-native version of the CLI/TUI setup flow.
It uses stable GET-addressable pages, but once a config is selected it keeps
the remaining setup on a single **Start Session** page so init mode and
service can be chosen in either order:

1. `index`: show a **Parameters** table with `Type` unset, then a
   **Select type** table.
2. `configs?type=...`: show `Type` filled and `Config` unset, then a
   **Select config** table showing config name and title.
3. `configs?type=...&config=...`: show `Type` and `Config` filled on a
   three-column **Start Session** page. The left column contains
   **Parameters**, **Start mode**, and **Service**; the center column contains
   **Manual provider/model** selection; the right column contains **Options**
   and the final **Start** action.
4. `configs?...&init=...`, `configs?...&service=...`,
   `configs?...&provider=...`, `configs?...&model=...`, or any combination of
   those parameters: remain on the same **Start Session** page, updating the
   selected values independently. Chat is not entered until the final
   **Start** link is followed, and neither service-selection order nor
   init-selection order is enforced.
5. On first entry to `configs?type=...&config=...`, Django defaults the
   start-mode selection to `new` and, if there are no manual option
   overrides in the query, defaults the service selection to the first
   non-invalid service entry.

The manual provider/model controls are single-select scrollable lists rather
than dropdowns because model lists may be long. Selecting a provider triggers
model lookup for that provider. When both provider and model are selected
manually, the page must allow **Start** without any service selection.

For direct session parameters such as `temperature`, `write_history`, and
`debug`, Django follows the same order-based model as other frontends. There
is no fixed precedence between a service file value and an explicit session
setting; whichever one was applied last is the effective value.

If any manual provider/model selection is present in the query, the service
radio list must render with no active selection even if a service value is
still present in the request state.

On the merged **Start Session** page, the visible **Parameters** table shows
only `Type` and `Config`. Their row headers are navigation controls: `Type`
returns to type selection and `Config` returns to config selection. This
replaces separate back links.

Django views build a fresh `Session` when constructing chat/persona requests:

```python
session = Session()
session.set_config(config_path)
if service_name:
    session.set_service(service_name)
else:
    session.provider_name = provider_name
    session.model_name = model_name
session.set_history(history_path)
persona = session.build()
```

Type IDs, config filenames, service ids, provider/model selections,
initialization mode, history filenames, and start filenames are passed as URL
query parameters (`GET`) or JSON body fields (`POST`). The Django layer
resolves filenames to full paths with `discover.resolve_path()` and
`discover.resolve_history_path()` before handing them to `Session`.

When a frontend needs to show or retain the history filename before the first
model request, it must ask the persona layer to allocate it. Django does this
by selecting config/service on a `Session`, or by selecting config plus manual
provider/model state, and calling `Session.create_history_path()`. Frontends
must not derive history filenames from display titles or local UI labels.

**Macro launch**: As an alternative to manual staged selection, the `index`
view also lists macros discovered via
`discover.find_macro_files()`.  Selecting a macro navigates to the `session`
view, which resolves the macro basename from `description_dirs` using
`discover.resolve_path()`, then applies it with
`Session.configure_from_macro_file()`. The persona-layer method uses the same
slash-command parser as CLI/TUI, so valid display-only commands produce no
output, parameter-setting commands for set type/set config/init/set service
must be in dependency order, later description-file selections may override
earlier `set temperature`, `set write-history`, and `set debug` commands, and
`start` stops macro processing. Errors are reported and terminate macro
execution. The `session` view redirects to the
appropriate staged setup page for the parameters selected by the macro. If
the macro selected an init mode or contained `start`, Django redirects to
the final **Start Session** page rather than entering chat immediately; the
user still clicks **Start**. Parser errors are reported as non-chat
`error-banner` messages.

Django frontends must support the same restart metadata model as terminal
frontends. A saved browser session should be loadable later from the history
directory with its transcript and resolved setup information intact.

For Django, session save/load is explicit:

- **Save** writes the current browser transcript to a history file in
  `history_dir`, along with header metadata for type, config, service, start
  file, mode, and mode-specific context such as resource files, hierarchy
  nodes, play files, project files, or profiles.
- **Load** reads a selected history file from `history_dir`, returns its turns
  to the browser, and uses header metadata to restore the same visible setup
  choices where possible.
- **New** creates an empty browser transcript and must not load prior turns.
  It may preselect the same config or resource collection, but it is not a
  resume operation.
- **Close session** saves any requested final state, releases the current
  ownership record when it matches the browser's `instance_id`, and marks the
  browser transcript as no longer active. Closing a session is distinct from
  starting `new`; it ends ownership of the current saved session.

The Django UI may keep transient chat state in the browser between requests,
but saved sessions are durable only after they have been written to
`history_dir`.

Direct `discover` calls by view:

| View | `discover` calls |
|------|-----------------|
| `index` | `find_types()`, `find_macro_files()` |
| `session` | `resolve_path()` (macro file); commands are applied through `Session.configure_from_macro_file()` and redirect to the appropriate staged setup URL |
| `configs` | `find_configs(type_filter=…)`, `get_services()`, `list_history_files()`, `find_start_files()`, `history_preview()`, `start_file_name()`, `start_file_display()`, `resolve_path()` |
| `services` | `find_service_files()` |
| `service` | `resolve_path()` |
| `models` | `find_providers()`, `list_models()` |
| `system` | `resolve_path()` |
| `chat` | `resolve_path()`, `resolve_history_path()`, `resolve_start_file()`, `start_file_body()`; new history filenames come from `Session.create_history_path()` |
| `_stream` | `resolve_path()`, `resolve_history_path()` |
| `_save_session` | `Session.create_history_path()` or `resolve_history_path()`, history write/update helpers |
| `_load_session` | `resolve_history_path()`, history read helpers |
| `_close_session` | `resolve_history_path()`, ownership metadata update helpers |
| `_edit_history` | `resolve_history_path()`, `build_session_for_history()`, `delete_history_file()` |
| `_delete_history` | `resolve_history_path()`, `delete_history_file()` |

Setup history selection and deletion in the CLI/TUI frontends should reuse one
history-matching path. The shared matcher operates over the same fields used
for history display: session display name, stored filename, basename, and
preview text. Read-only commands may list every match; state-changing commands
must require a unique match and treat multiple matches as an error.

---

## 4. Persona Construction

All paths converge on `discover.build_config()`:

```python
config = discover.build_config(
    config_path, service_name, history_path, start_file_path, provider_name, model_name
)
persona = Persona(config)
```

`build_config()` applies the load order (`default.json` → type file →
config file → service file) and calls `config._resolve_system()` to
produce the final system prompt. When manual provider/model selection is used,
the service-file step is skipped and those values are written directly to
`config.service`. `Persona.__init__()` selects the LLM backend from
`config.service.provider`, loads any existing history file, and injects the
start-file message if the history is empty.

---

## 5. Streaming and History

### `llemon-tui`

`CursesChatView` is constructed by the CLI setup engine with display data and
callbacks that adapt entered text to the active `Persona`:

```python
CursesChatView(
    header=persona.header,
    user_tag=persona.user_tag,
    assistant_tag=persona.assistant_tag,
    history=persona.display_history(),
    on_submit=persona.stream,
    on_cleanup=persona.discard_if_empty,
).run()
```

The curses main loop calls the supplied submit callback and appends each chunk
to the display as it arrives.

### `llemon-cli`

`llemon-cli` calls `persona.stream(user_input)` directly in its own
word-wrapping print loop.

### Django

Streaming is done over Server-Sent Events from the `_stream` POST
endpoint. The browser sends the new user input plus the selected session
parameters. When a saved `history_path` is supplied, `_stream` rebuilds the
persona against that history file and the backend history is authoritative.
The client does not send `history_data` on each request.

For unsaved browser-only sessions, Django may still keep transient transcript
state in the browser or request scope, but that state is frontend-local and
is not the archive of record. Once a history file exists, all further stream,
edit, and delete operations must target the saved `.jsonl` transcript rather
than a client-supplied history copy.

For structured-output personas with a resolved `download_field`, `_stream`
includes a `download` object in the final SSE event when
`Persona.download_payload()` returns one. The browser should render this as a
download link associated with the assistant response. The payload shape is:

```json
{ "field": "code", "content": "..." }
```

No `download` object is emitted when structured output is inactive, the
configured field is absent, or the field value is null or empty.

Client-side state is not the archive of record. Django save/load endpoints
should bridge browser state to the same history-directory storage used by the
terminal frontends:

- `_save_session` accepts the current transcript plus restart metadata and
  writes or updates a history file. New files are named by the persona layer
  through `Session.create_history_path()`.
- `_load_session` reads a saved history file and returns both turns and restart
  metadata to the browser.
- `_close_session` releases the ownership record for the current browser
  `instance_id`. If another owner has already taken over, close must not clear
  that newer owner.
- `_stream` may use in-memory history only for an unsaved transient browser
  session. When a saved history path is supplied, it must preserve and use
  that path as the authoritative transcript for later stream, edit, delete,
  and restart operations.

Start files follow the same rule as terminal frontends: they are injected only
for a fresh session and are not re-injected when loading saved history.

---

## 6. Error Reporting

Error display follows the per-frontend rules defined in the overview:

| Frontend | Error display |
|----------|--------------|
| `llemon-cli` | `print()` inline in the terminal |
| `llemon-tui` chat screen | Non-editable `[!]` line inserted in the chat stream |
| `llemon-tui` setup screen | Separate persistent region in the panel; user presses Esc to clear input |
| Django chat page | `#status` element (fixed position, separate from chat); click `×` to dismiss |
| Django non-chat pages | Inline `error-banner` element with a `×` dismiss button |

The `llemon-tui` chat screen also handles backend control chunks with
`event: "retry"`. When one is received, any partially displayed assistant
message for the active turn is erased and replaced with a non-editable
`[!]` retry status line. The retry status is transient: subsequent
assistant content or the final failure message replaces it.

All unexpected exceptions are logged via the `logging` module before any
user-facing message is shown.  Full details (tracebacks) go to the log
only.

---

## 7. Import Summary by Frontend

### `llemon-tui`

```python
from hty7.config import AppConfig, ConfigError as _ConfigError
from hty7.llemon.cli import run_tui_app
```

### `llemon-cli`

```python
from hty7.config import AppConfig, ConfigError as _ConfigError
from hty7.llemon.cli import main
```

### Session titles

All three frontends expose a way to override the auto-generated session title
stored in the history header `description` field.

- **`llemon-cli`** — `/title TITLE` in the chat REPL calls
  `persona.set_name(title)`.  `/title` with no argument prints the current
  title.
- **`llemon-tui`** — `/title TITLE` calls `persona.set_name(title)`.
  `/title` with no argument appends the current title as an informational line
  in the history area.
- **Django** — the chat page renders an editable "Session name:" field above
  the history area, pre-populated from the history header.  Changes are
  persisted via `POST /set-name/` which calls `History.set_name()` on the
  file.  The field auto-saves on blur and after a short debounce while typing.

`Persona` exposes `get_name() -> str` and `set_name(name: str) -> None`,
forwarding to the backend `History` object, which is the authoritative owner of
the `description` field.

### Django (`llemon_djview`)

```python
from ..persona.config import Config, ConfigError
from ..persona.service import Service
from ..persona.session import Session
from ..persona import discover
```

---

## 8. Differences at a Glance

| Aspect | `llemon-tui` | `llemon-cli` | Django |
|--------|--------------|--------------|--------|
| Calls `discover.init()` | yes (via `cli.run_tui_app()`) | yes (via `cli.run_cli_app()`) | no (host's responsibility) |
| Config selection | `Session` via curses setup or macro arg | `Session` via REPL or macro arg | Staged browser setup pages; macro via `index` + `session` view redirects into the same staged flow |
| CWD in discovery | only if `"."` in `description_dirs` | only if `"."` in `description_dirs` | only if `"."` in `description_dirs` |
| Persona lifetime | one per `start` command | one per `start` command | one per SSE request |
| History authority | file | file | client (browser sends full list each request) |
| Streaming transport | curses screen updates | `print()` with word wrap | Server-Sent Events |

---

## 7. Setup Parameter Selection

### Parameter Ordering

Setup parameters can be selected in flexible order with automatic cascading:

1. **Type** is a prerequisite (clears config, service, history when changed)
2. **Config and Service** can be set in any order:
   - Both set provider/model, template, and other config values
   - If both set overlapping parameters, last one wins
3. **Start mode** (new/file/history) is independent:
   - `set start file` lists all available start files (without config constraint)
   - Selecting a start file automatically sets type and config from the file's headers
   - `set start history` lists all history files (without config constraint)
   - Selecting a history entry automatically sets config from the history's `config_name` header field

### History File Format Change

History files (`.jsonl`) now include a `config_name` field in the header JSON:

```json
{
  "type": "header",
  "model": "gpt-4o",
  "config_name": "openai",
  "template_id": "...",
  "template_name": "...",
  ...
}
```

This allows history entries to be discovered and selected without requiring the config to be pre-set.

### New History Commands

Two new commands for editing history metadata:

- `set title history <NUMBER or ID> <TITLE>` — Set the title/description of a history entry
  - NUMBER is a 0-based index in the current history list
  - ID is the `history_id` field from the history header
  - TITLE is the new title text (no quotes needed)

- `set id history <NUMBER>` — Set the ID of a history entry
  - NUMBER is a 0-based index in the current history list
  - Sets the `history_id` field in the history header

Both commands work with any history file, not just the currently-loaded session.

### Setup UI Display (TUI)

The `llemon-tui` setup screen now displays:

1. **Fixed header area** with all session variables:
   - Type, Config, Start, Service
   - Provider, Model, Temperature, Wrap, Write-History, Debug

2. **Separator line** below the header

3. **Main content area** below, containing:
   - Command history and output
   - Context information lists (type, config, service, start mode)
   - User input prompt (❯)

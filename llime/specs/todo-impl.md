# To Do App Implementation

## App Structure

- Django app: `to_do_list`
- Settings registration: `config/settings.py`
- URL mount: `config/urls.py` at `/to-do-list/`

## Main Files

- `to_do_list/models.py`: `Category` and `Item` models
- `to_do_list/views.py`: page rendering and POST handlers
- `to_do_list/urls.py`: app routes
- `to_do_list/templates/to_do_list/index.html`: main page
- `base/static/base/css/stylesheet.css`: shared styling, including To Do layout

## Model Notes

### Category

- Stored as a standalone table.
- `is_active` persists the current selection.
- Ordered alphabetically by `name`.

### Item

- Stored in the app database table created by migrations.
- Uses a text `category` field rather than a foreign key to `Category`.
- Ordered by starred/urgent first, then unfinished before finished, then due date, then higher priority, then title.

## View Flow

### `index`

- Calls `_render_index()`.
- Loads the active category via `_ensure_active_category()`.
- Filters the item list to the active category.
- Loads categories in alphabetical order.

### `new_category`

- POST only in practice.
- Validates non-empty unique name.
- Clears any previous active category.
- Creates the new category as active.

### `select_category`

- Marks the chosen category active.
- Clears `is_active` on all others.

### `new_item`

- Validates non-empty title.
- Validates the submitted category name if present.
- Creates the item with current defaults:
  - `due_date = date.today()`
  - `priority = 3`

### `toggle_item_finished`

- Updates `Item.finished` from the submitted checkbox value.

## Templates and Styling

- The page extends `base/base.html`.
- Shared left navbar entries come from `base.lib.tools.nav`.
- The main page uses a two-column flex layout with a mobile stack fallback.
- Category selection and item completion both use small POST forms with CSRF
  tokens.
- Completed items receive a `todo-finished` CSS class on the `<li>`, which
  applies `text-decoration: line-through` and `font-weight: normal` to the
  `.todo-title` span.

## Migrations

Current app migrations include:

- `0001_initial`
- `0002_alter_item_options_item_finished_item_repeat_and_more`
- `0003_category`
- `0004_alter_category_options_category_is_active`
- `0005_alter_category_options`

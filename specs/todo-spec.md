# To Do App Spec

## Purpose

`to_do_list` is a standalone to-do application inside this Django project.

## URL

- Base URL: `/to-do-list/`

## Data Model

### Category

- `name`: unique text name
- `is_active`: boolean flag indicating the selected category

Exactly one category should be active when categories exist. The active category
must persist in the database and be restored on refresh.

### Item

- `title`: item text
- `due_date`: due date
- `priority`: integer `1..4`, where `4` is highest
- `finished`: boolean completion flag
- `note`: free-form note text
- `category`: category name as text
- `repeat`: boolean
- `repeat_interval`: positive integer
- `repeat_from_due_date`: boolean

## UI Behavior

- The app uses the shared site navbar.
- The page has two columns on desktop:
  - left: categories
  - right: items
- The category list stays in alphabetical order.
- The active category is highlighted.
- The right column shows only items in the active category.
- Each item row includes a checkbox bound to `finished`.
- Completed items display with a strikethrough and normal (non-bold) font weight.
- Completed items sort to the end of the list.
- The left column includes a `New Category` control that opens a panel asking
  the user to choose a name.
- The right column includes a `New Item` control that opens a panel for item text
  and category selection.

## Current Creation Defaults

The quick-create item panel currently sets:

- `due_date` to the current date
- `priority` to `3`

Those defaults are implementation choices for the current UI, not a final UX
decision.

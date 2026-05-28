from calendar import monthrange
from datetime import date, timedelta, time

from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils import timezone

from base.lib.tools import nav as base_nav
from .models import Category, Item

VIRTUAL_CATEGORIES = {
    'Past Due Items': 'past_due',
    'Warning and Past Due Items': 'warning_and_past_due',
}
REPEAT_UNIT_CHOICES = Item.REPEAT_UNIT_CHOICES


def _add_months(base_date, months):
    year = base_date.year + (base_date.month - 1 + months) // 12
    month = (base_date.month - 1 + months) % 12 + 1
    day = min(base_date.day, monthrange(year, month)[1])
    return date(year, month, day)


def _advance_due_date(base_date, interval, unit):
    if unit == Item.REPEAT_WEEKS:
        return base_date + timedelta(weeks=interval)
    if unit == Item.REPEAT_MONTHS:
        return _add_months(base_date, interval)
    if unit == Item.REPEAT_YEARS:
        return _add_months(base_date, interval * 12)
    return base_date + timedelta(days=interval)


def _next_repeat_due_date(item):
    if item.repeat_from_due_date:
        base_date = item.due_date
    else:
        base_date = timezone.localdate()
    return _advance_due_date(base_date, item.repeat_interval, item.repeat_unit)


def _matching_generated_repeat_items(item, due_date):
    return Item.objects.filter(
        id__gt=item.id,
        title=item.title,
        due_date=due_date,
        due_time=item.due_time,
        warning_days=item.warning_days,
        priority=item.priority,
        finished=False,
        starred=False,
        note=item.note,
        category=item.category,
        repeat=item.repeat,
        repeat_interval=item.repeat_interval,
        repeat_unit=item.repeat_unit,
        repeat_from_due_date=item.repeat_from_due_date,
    ).order_by('id')


def _item_sort_key(item):
    display_starred = not item.finished and (item.starred or item.warning_active or item.overdue)
    return (
        0 if display_starred else 1,
        item.finished,
        item.due_date,
        item.due_time,
        -item.priority,
        item.title,
    )


def _sort_items(items):
    return sorted(items, key=_item_sort_key)


def _ensure_active_category():
    active = Category.objects.filter(is_active=True).order_by('id').first()
    if active is not None:
        Category.objects.exclude(pk=active.pk).filter(is_active=True).update(is_active=False)
        return active

    first_category = Category.objects.order_by('name', 'id').first()
    if first_category is not None:
        first_category.is_active = True
        first_category.save(update_fields=['is_active'])
    return first_category


def _render_index(
    request,
    *,
    show_new_category_panel=False,
    new_category_error='',
    new_category_name='',
    show_edit_category_panel=False,
    edit_category_id=None,
    edit_category_error='',
    edit_category_name=None,
    show_new_item_panel=False,
    new_item_error='',
    new_item_title='',
    new_item_category='',
    new_item_due_date='',
    new_item_due_time='',
    new_item_priority='',
    new_item_warning_days='',
    new_item_repeat_mode='none',
    new_item_repeat_interval='1',
    new_item_repeat_unit=Item.REPEAT_DAYS,
    show_edit_item_panel=False,
    edit_item_id=None,
    edit_item_error='',
    edit_item_title=None,
    edit_item_category=None,
    edit_item_due_date=None,
    edit_item_due_time=None,
    edit_item_priority=None,
    edit_item_warning_days=None,
    edit_item_repeat_mode=None,
    edit_item_repeat_interval=None,
    edit_item_repeat_unit=None,
    edit_item_note=None,
    active_virtual_category=None,
):
    active_category = None if active_virtual_category else _ensure_active_category()
    categories = Category.objects.order_by('name', 'id')
    editing_category = None
    editing_item = None
    if edit_category_id is not None:
        editing_category = Category.objects.filter(pk=edit_category_id).first()
        if editing_category is None:
            show_edit_category_panel = False
            edit_category_id = None
        elif edit_category_name is None:
            edit_category_name = editing_category.name
    if edit_item_id is not None:
        editing_item = Item.objects.filter(pk=edit_item_id).first()
        if editing_item is None:
            show_edit_item_panel = False
            edit_item_id = None
        else:
            if edit_item_title is None:
                edit_item_title = editing_item.title
            if edit_item_category is None:
                edit_item_category = editing_item.category
            if edit_item_due_date is None:
                edit_item_due_date = editing_item.due_date.isoformat()
            if edit_item_due_time is None:
                edit_item_due_time = editing_item.due_time.isoformat(timespec='minutes')
            if edit_item_priority is None:
                edit_item_priority = str(editing_item.priority)
            if edit_item_warning_days is None:
                if editing_item.warning_days is not None:
                    edit_item_warning_days = str(editing_item.warning_days)
                else:
                    edit_item_warning_days = ''
            if edit_item_repeat_mode is None:
                if editing_item.repeat:
                    edit_item_repeat_mode = 'due' if editing_item.repeat_from_due_date else 'checked'
                else:
                    edit_item_repeat_mode = 'none'
            if edit_item_repeat_interval is None:
                edit_item_repeat_interval = str(editing_item.repeat_interval)
            if edit_item_repeat_unit is None:
                edit_item_repeat_unit = editing_item.repeat_unit
            if edit_item_note is None:
                edit_item_note = editing_item.note

    if active_virtual_category:
        if active_virtual_category == 'past_due':
            items = _sort_items([item for item in Item.objects.all() if item.overdue])
        elif active_virtual_category == 'warning_and_past_due':
            items = _sort_items([item for item in Item.objects.all() if item.warning_active or item.overdue])
        else:
            items = []
    elif active_category is None:
        items = _sort_items(Item.objects.filter(category=''))
    else:
        items = _sort_items(Item.objects.filter(category=active_category.name))

    # Find categories with starred/warning/overdue items for side markers.
    starred_categories = set()
    warning_categories = set()
    overdue_categories = set()
    for category in categories:
        category_items = Item.objects.filter(category=category.name)
        if any(item.starred for item in category_items):
            starred_categories.add(category.id)
        if any(item.warning_active for item in category_items):
            warning_categories.add(category.id)
        if any(item.overdue for item in category_items):
            overdue_categories.add(category.id)

    # Default due_date to one week from now if not provided
    if not new_item_due_date:
        new_item_due_date = (timezone.localdate() + timedelta(days=7)).isoformat()
    if not new_item_due_time:
        new_item_due_time = timezone.localtime().time().isoformat(timespec='minutes')

    return render(request, 'to_do_list/index.html', {
        'title': 'To Do',
        'base_nav': base_nav,
        'nav': [],
        'items': items,
        'categories': categories,
        'active_category': active_category,
        'active_virtual_category': active_virtual_category,
        'virtual_categories': VIRTUAL_CATEGORIES,
        'starred_categories': starred_categories,
        'warning_categories': warning_categories,
        'overdue_categories': overdue_categories,
        'show_new_category_panel': show_new_category_panel,
        'new_category_error': new_category_error,
        'new_category_name': new_category_name,
        'show_edit_category_panel': show_edit_category_panel,
        'editing_category': editing_category,
        'edit_category_error': edit_category_error,
        'edit_category_name': edit_category_name,
        'show_new_item_panel': show_new_item_panel,
        'new_item_error': new_item_error,
        'new_item_title': new_item_title,
        'new_item_category': new_item_category or (active_category.name if active_category else ''),
        'new_item_due_date': new_item_due_date,
        'new_item_due_time': new_item_due_time,
        'new_item_priority': new_item_priority,
        'new_item_warning_days': new_item_warning_days,
        'new_item_repeat_mode': new_item_repeat_mode,
        'new_item_repeat_interval': new_item_repeat_interval,
        'new_item_repeat_unit': new_item_repeat_unit,
        'show_edit_item_panel': show_edit_item_panel,
        'editing_item': editing_item,
        'edit_item_error': edit_item_error,
        'edit_item_title': edit_item_title,
        'edit_item_category': edit_item_category,
        'edit_item_due_date': edit_item_due_date,
        'edit_item_due_time': edit_item_due_time,
        'edit_item_priority': edit_item_priority,
        'edit_item_warning_days': edit_item_warning_days,
        'edit_item_repeat_mode': edit_item_repeat_mode,
        'edit_item_repeat_interval': edit_item_repeat_interval,
        'edit_item_repeat_unit': edit_item_repeat_unit,
        'edit_item_note': edit_item_note,
        'priority_choices': Item.PRIORITY_CHOICES,
        'repeat_unit_choices': REPEAT_UNIT_CHOICES,
    })


def index(request):
    active_virtual_category = request.session.get('active_virtual_category')
    edit_category_id = request.GET.get('edit_category')
    edit_item_id = request.GET.get('edit_item')
    if edit_category_id:
        try:
            edit_category_id = int(edit_category_id)
        except (TypeError, ValueError):
            edit_category_id = None
    else:
        edit_category_id = None
    if edit_item_id:
        try:
            edit_item_id = int(edit_item_id)
        except (TypeError, ValueError):
            edit_item_id = None
    else:
        edit_item_id = None
    if active_virtual_category:
        Category.objects.update(is_active=False)
    return _render_index(
        request,
        show_new_category_panel=request.GET.get('new_category') == '1',
        show_edit_category_panel=edit_category_id is not None,
        edit_category_id=edit_category_id,
        show_new_item_panel=request.GET.get('new_item') == '1',
        show_edit_item_panel=edit_item_id is not None,
        edit_item_id=edit_item_id,
        active_virtual_category=active_virtual_category,
    )


def new_category(request):
    if request.method != 'POST':
        return redirect('to_do_list:index')

    name = request.POST.get('name', '').strip()

    if not name:
        return _render_index(
            request,
            show_new_category_panel=True,
            new_category_error='Choose a name.',
        )

    if Category.objects.filter(name=name).exists():
        return _render_index(
            request,
            show_new_category_panel=True,
            new_category_error='That category already exists.',
            new_category_name=name,
        )

    Category.objects.update(is_active=False)
    request.session.pop('active_virtual_category', None)
    Category.objects.create(name=name, is_active=True)
    return redirect('to_do_list:index')


def update_category(request, category_id):
    if request.method != 'POST':
        return redirect('to_do_list:index')

    category = get_object_or_404(Category, pk=category_id)
    name = request.POST.get('name', '').strip()

    if not name:
        return _render_index(
            request,
            show_edit_category_panel=True,
            edit_category_id=category.id,
            edit_category_error='Choose a name.',
            edit_category_name=name,
            active_virtual_category=request.session.get('active_virtual_category'),
        )

    if Category.objects.exclude(pk=category.pk).filter(name=name).exists():
        return _render_index(
            request,
            show_edit_category_panel=True,
            edit_category_id=category.id,
            edit_category_error='That category already exists.',
            edit_category_name=name,
            active_virtual_category=request.session.get('active_virtual_category'),
        )

    old_name = category.name
    with transaction.atomic():
        category.name = name
        category.save(update_fields=['name'])
        Item.objects.filter(category=old_name).update(category=name)

    return redirect('to_do_list:index')


def new_item(request):
    if request.method != 'POST':
        return redirect('to_do_list:index')

    title = request.POST.get('title', '').strip()
    category_name = request.POST.get('category', '').strip()
    due_date_str = request.POST.get('due_date', '').strip()
    due_time_str = request.POST.get('due_time', '').strip()
    priority_str = request.POST.get('priority', '').strip()
    warning_days_str = request.POST.get('warning_days', '').strip()
    repeat_mode = request.POST.get('repeat_mode', 'none').strip()
    repeat_interval_str = request.POST.get('repeat_interval', '1').strip()
    repeat_unit = request.POST.get('repeat_unit', Item.REPEAT_DAYS).strip()

    if not title:
        return _render_index(
            request,
            show_new_item_panel=True,
            new_item_error='Choose item text.',
            new_item_title=title,
            new_item_category=category_name,
            new_item_due_date=due_date_str,
            new_item_due_time=due_time_str,
            new_item_priority=priority_str,
            new_item_warning_days=warning_days_str,
            new_item_repeat_mode=repeat_mode,
            new_item_repeat_interval=repeat_interval_str,
            new_item_repeat_unit=repeat_unit,
        )

    if category_name and not Category.objects.filter(name=category_name).exists():
        return _render_index(
            request,
            show_new_item_panel=True,
            new_item_error='Choose a valid category.',
            new_item_title=title,
            new_item_category='',
            new_item_due_date=due_date_str,
            new_item_due_time=due_time_str,
            new_item_priority=priority_str,
            new_item_warning_days=warning_days_str,
            new_item_repeat_mode=repeat_mode,
            new_item_repeat_interval=repeat_interval_str,
            new_item_repeat_unit=repeat_unit,
        )

    # Validate priority
    try:
        priority = int(priority_str) if priority_str else Item.IMPORTANT
        valid_priorities = [choice[0] for choice in Item.PRIORITY_CHOICES]
        if priority not in valid_priorities:
            raise ValueError()
    except (ValueError, TypeError):
        return _render_index(
            request,
            show_new_item_panel=True,
            new_item_error='Choose a valid priority.',
            new_item_title=title,
            new_item_category=category_name,
            new_item_due_date=due_date_str,
            new_item_due_time=due_time_str,
            new_item_priority=priority_str,
            new_item_warning_days=warning_days_str,
            new_item_repeat_mode=repeat_mode,
            new_item_repeat_interval=repeat_interval_str,
            new_item_repeat_unit=repeat_unit,
        )

    # Validate warning_days
    warning_days = None
    if warning_days_str:
        try:
            warning_days = int(warning_days_str)
            if warning_days < 1:
                raise ValueError()
        except (ValueError, TypeError):
            return _render_index(
                request,
                show_new_item_panel=True,
                new_item_error='Warning days must be a positive number.',
                new_item_title=title,
                new_item_category=category_name,
                new_item_due_date=due_date_str,
                new_item_due_time=due_time_str,
                new_item_priority=priority_str,
                new_item_warning_days=warning_days_str,
                new_item_repeat_mode=repeat_mode,
                new_item_repeat_interval=repeat_interval_str,
                new_item_repeat_unit=repeat_unit,
            )

    # Validate repeat_mode and repeat_interval
    if repeat_mode not in ('none', 'checked', 'due'):
        repeat_mode = 'none'
    if repeat_unit not in dict(REPEAT_UNIT_CHOICES):
        repeat_unit = Item.REPEAT_DAYS

    repeat = repeat_mode != 'none'
    repeat_interval = 1
    repeat_from_due_date = True

    if repeat:
        if repeat_interval_str:
            try:
                repeat_interval = int(repeat_interval_str)
                if repeat_interval < 1:
                    raise ValueError()
            except (ValueError, TypeError):
                return _render_index(
                    request,
                    show_new_item_panel=True,
                    new_item_error='Repeat interval must be a positive number.',
                    new_item_title=title,
                    new_item_category=category_name,
                    new_item_due_date=due_date_str,
                    new_item_due_time=due_time_str,
                    new_item_priority=priority_str,
                    new_item_warning_days=warning_days_str,
                    new_item_repeat_mode=repeat_mode,
                    new_item_repeat_interval=repeat_interval_str,
                    new_item_repeat_unit=repeat_unit,
                )
        repeat_from_due_date = repeat_mode == 'due'

    # Parse due_date and due_time
    try:
        if due_date_str:
            due_date = date.fromisoformat(due_date_str)
        else:
            due_date = timezone.localdate() + timedelta(days=7)

        if due_time_str:
            due_time = time.fromisoformat(due_time_str)
        else:
            due_time = time(0, 0)
    except ValueError:
        return _render_index(
            request,
            show_new_item_panel=True,
            new_item_error='Invalid date or time format.',
            new_item_title=title,
            new_item_category=category_name,
            new_item_due_date=due_date_str,
            new_item_due_time=due_time_str,
            new_item_priority=priority_str,
            new_item_warning_days=warning_days_str,
            new_item_repeat_mode=repeat_mode,
            new_item_repeat_interval=repeat_interval_str,
            new_item_repeat_unit=repeat_unit,
        )

    Item.objects.create(
        title=title,
        category=category_name,
        due_date=due_date,
        due_time=due_time,
        priority=priority,
        warning_days=warning_days,
        repeat=repeat,
        repeat_interval=repeat_interval,
        repeat_unit=repeat_unit,
        repeat_from_due_date=repeat_from_due_date,
    )
    return redirect('to_do_list:index')


def update_item(request, item_id):
    if request.method != 'POST':
        return redirect('to_do_list:index')

    item = get_object_or_404(Item, pk=item_id)
    title = request.POST.get('title', '').strip()
    category_name = request.POST.get('category', '').strip()
    due_date_str = request.POST.get('due_date', '').strip()
    due_time_str = request.POST.get('due_time', '').strip()
    priority_str = request.POST.get('priority', '').strip()
    warning_days_str = request.POST.get('warning_days', '').strip()
    repeat_mode = request.POST.get('repeat_mode', 'none').strip()
    repeat_interval_str = request.POST.get('repeat_interval', '1').strip()
    repeat_unit = request.POST.get('repeat_unit', Item.REPEAT_DAYS).strip()
    note = request.POST.get('note', '')
    render_kwargs = {
        'show_edit_item_panel': True,
        'edit_item_id': item.id,
        'edit_item_title': title,
        'edit_item_category': category_name,
        'edit_item_due_date': due_date_str,
        'edit_item_due_time': due_time_str,
        'edit_item_priority': priority_str,
        'edit_item_warning_days': warning_days_str,
        'edit_item_repeat_mode': repeat_mode,
        'edit_item_repeat_interval': repeat_interval_str,
        'edit_item_repeat_unit': repeat_unit,
        'edit_item_note': note,
        'active_virtual_category': request.session.get('active_virtual_category'),
    }

    if not title:
        return _render_index(request, edit_item_error='Choose item text.', **render_kwargs)

    if category_name and not Category.objects.filter(name=category_name).exists():
        render_kwargs['edit_item_category'] = ''
        return _render_index(request, edit_item_error='Choose a valid category.', **render_kwargs)

    try:
        priority = int(priority_str) if priority_str else Item.IMPORTANT
        valid_priorities = [choice[0] for choice in Item.PRIORITY_CHOICES]
        if priority not in valid_priorities:
            raise ValueError()
    except (ValueError, TypeError):
        return _render_index(request, edit_item_error='Choose a valid priority.', **render_kwargs)

    warning_days = None
    if warning_days_str:
        try:
            warning_days = int(warning_days_str)
            if warning_days < 1:
                raise ValueError()
        except (ValueError, TypeError):
            return _render_index(request, edit_item_error='Warning days must be a positive number.', **render_kwargs)

    if repeat_mode not in ('none', 'checked', 'due'):
        repeat_mode = 'none'
        render_kwargs['edit_item_repeat_mode'] = repeat_mode
    if repeat_unit not in dict(REPEAT_UNIT_CHOICES):
        repeat_unit = Item.REPEAT_DAYS
        render_kwargs['edit_item_repeat_unit'] = repeat_unit

    repeat = repeat_mode != 'none'
    repeat_interval = 1
    repeat_from_due_date = True
    if repeat:
        if repeat_interval_str:
            try:
                repeat_interval = int(repeat_interval_str)
                if repeat_interval < 1:
                    raise ValueError()
            except (ValueError, TypeError):
                return _render_index(request, edit_item_error='Repeat interval must be a positive number.', **render_kwargs)
        repeat_from_due_date = repeat_mode == 'due'

    try:
        if due_date_str:
            due_date = date.fromisoformat(due_date_str)
        else:
            due_date = timezone.localdate() + timedelta(days=7)

        if due_time_str:
            due_time = time.fromisoformat(due_time_str)
        else:
            due_time = time(0, 0)
    except ValueError:
        return _render_index(request, edit_item_error='Invalid date or time format.', **render_kwargs)

    item.title = title
    item.category = category_name
    item.due_date = due_date
    item.due_time = due_time
    item.priority = priority
    item.warning_days = warning_days
    item.repeat = repeat
    item.repeat_interval = repeat_interval
    item.repeat_unit = repeat_unit
    item.repeat_from_due_date = repeat_from_due_date
    item.note = note
    item.save()
    return redirect('to_do_list:index')


def toggle_item_finished(request, item_id):
    if request.method != 'POST':
        return redirect('to_do_list:index')

    finished = request.POST.get('finished') == 'on'

    with transaction.atomic():
        item = get_object_or_404(Item.objects.select_for_update(), pk=item_id)
        was_finished = item.finished

        if item.repeat:
            new_due_date = _next_repeat_due_date(item)

            if finished and not was_finished:
                item.finished = True
                item.save(update_fields=['finished'])
                Item.objects.create(
                    title=item.title,
                    due_date=new_due_date,
                    due_time=item.due_time,
                    warning_days=item.warning_days,
                    priority=item.priority,
                    finished=False,
                    note=item.note,
                    category=item.category,
                    repeat=item.repeat,
                    repeat_interval=item.repeat_interval,
                    repeat_unit=item.repeat_unit,
                    repeat_from_due_date=item.repeat_from_due_date,
                )
            elif not finished and was_finished:
                generated_item = _matching_generated_repeat_items(item, new_due_date).first()
                if generated_item is not None:
                    generated_item.delete()
                item.finished = False
                item.save(update_fields=['finished'])
        elif item.finished != finished:
            item.finished = finished
            item.save(update_fields=['finished'])

    return redirect('to_do_list:index')


def toggle_item_starred(request, item_id):
    if request.method != 'POST':
        return redirect('to_do_list:index')

    item = get_object_or_404(Item, pk=item_id)
    item.starred = not item.starred
    item.save(update_fields=['starred'])
    return redirect('to_do_list:index')


def select_category(request, category_id):
    if request.method != 'POST':
        return redirect('to_do_list:index')

    category = get_object_or_404(Category, pk=category_id)
    request.session.pop('active_virtual_category', None)
    Category.objects.exclude(pk=category.pk).update(is_active=False)
    if not category.is_active:
        category.is_active = True
        category.save(update_fields=['is_active'])
    return redirect('to_do_list:index')


def select_virtual_category(request, virtual_category):
    if request.method != 'POST':
        return redirect('to_do_list:index')

    if virtual_category not in VIRTUAL_CATEGORIES.values():
        return redirect('to_do_list:index')

    Category.objects.update(is_active=False)
    request.session['active_virtual_category'] = virtual_category
    return redirect('to_do_list:index')


def delete_item(request, item_id):
    if request.method != 'POST':
        return redirect('to_do_list:index')

    item = get_object_or_404(Item, pk=item_id)
    item.delete()
    return redirect('to_do_list:index')


def delete_finished_items(request):
    if request.method != 'POST':
        return redirect('to_do_list:index')

    Item.objects.filter(finished=True).delete()
    return redirect('to_do_list:index')

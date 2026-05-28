from datetime import date, timedelta
from django.test import TestCase, Client
from django.utils import timezone
from .models import Item, Category


class VirtualCategoriesTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.category = Category.objects.create(name='Test', is_active=True)
        today = date.today()

        # Create test items
        self.overdue_1 = Item.objects.create(
            title='Overdue Task 1',
            category='Test',
            due_date=today - timedelta(days=1),
            priority=Item.IMPORTANT,
            finished=False
        )

        self.overdue_2 = Item.objects.create(
            title='Overdue Task 2',
            category='Test',
            due_date=today - timedelta(days=5),
            priority=Item.SEVERE,
            finished=False
        )

        self.warning_task = Item.objects.create(
            title='Warning Task',
            category='Test',
            due_date=today + timedelta(days=2),
            warning_days=3,
            priority=Item.IMPORTANT,
            finished=False
        )

        self.warning_and_overdue = Item.objects.create(
            title='Warning and Overdue Task',
            category='Test',
            due_date=today - timedelta(days=2),
            warning_days=5,
            priority=Item.SEVERE,
            finished=False
        )

        self.normal_task = Item.objects.create(
            title='Normal Task',
            category='Test',
            due_date=today + timedelta(days=10),
            priority=Item.IMPORTANT,
            finished=False
        )

    def test_past_due_items_virtual_category_appears(self):
        """Test that Past Due Items virtual category appears in the UI"""
        response = self.client.get('/to-do-list/')
        self.assertContains(response, 'Past Due Items')

    def test_warning_and_past_due_items_virtual_category_appears(self):
        """Test that Warning and Past Due Items virtual category appears in the UI"""
        response = self.client.get('/to-do-list/')
        self.assertContains(response, 'Warning and Past Due Items')

    def test_past_due_items_filter(self):
        """Test that Past Due Items virtual category shows correct items"""
        session = self.client.session
        session['active_virtual_category'] = 'past_due'
        session.save()

        response = self.client.get('/to-do-list/')

        self.assertContains(response, 'Overdue Task 1')
        self.assertContains(response, 'Overdue Task 2')
        self.assertContains(response, 'Warning and Overdue Task')
        self.assertNotContains(response, 'Normal Task')

    def test_warning_and_past_due_items_filter(self):
        """Test that Warning and Past Due Items shows correct items"""
        session = self.client.session
        session['active_virtual_category'] = 'warning_and_past_due'
        session.save()

        response = self.client.get('/to-do-list/')

        self.assertContains(response, 'Overdue Task 1')
        self.assertContains(response, 'Overdue Task 2')
        self.assertContains(response, 'Warning and Overdue Task')
        self.assertContains(response, 'Warning Task')
        self.assertNotContains(response, 'Normal Task')

    def test_virtual_category_does_not_reactivate_real_category_on_render(self):
        session = self.client.session
        session['active_virtual_category'] = 'past_due'
        session.save()

        response = self.client.get('/to-do-list/')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Category.objects.filter(is_active=True).exists())

    def test_new_category_clears_virtual_category_selection(self):
        session = self.client.session
        session['active_virtual_category'] = 'past_due'
        session.save()

        response = self.client.post('/to-do-list/categories/new/', {'name': 'Errands'})

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('active_virtual_category', self.client.session)
        self.assertTrue(Category.objects.get(name='Errands').is_active)
        self.assertFalse(Category.objects.get(name='Test').is_active)

    def test_delete_button_appears_on_items(self):
        """Test that edit button appears on each item row"""
        response = self.client.get('/to-do-list/')
        self.assertContains(response, 'todo-item-edit-button')
        self.assertContains(response, 'Edit')

    def test_star_button_appears_on_items(self):
        response = self.client.get('/to-do-list/')
        self.assertContains(response, 'todo-star-button')
        self.assertContains(response, '☆')

    def test_delete_finished_items_button_appears(self):
        """Test that delete finished items button appears"""
        response = self.client.get('/to-do-list/')
        self.assertContains(response, 'Delete Finished')
        self.assertContains(response, 'Delete all completed items?')

    def test_delete_individual_item(self):
        """Test deleting a single item"""
        item_id = self.normal_task.id
        initial_count = Item.objects.count()

        response = self.client.post(f'/to-do-list/items/{item_id}/delete/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Item.objects.count(), initial_count - 1)
        self.assertFalse(Item.objects.filter(id=item_id).exists())

    def test_delete_finished_items(self):
        """Test deleting all finished items"""
        # Mark some items as finished
        self.normal_task.finished = True
        self.normal_task.save()
        self.warning_task.finished = True
        self.warning_task.save()

        initial_count = Item.objects.count()
        finished_count = Item.objects.filter(finished=True).count()

        response = self.client.post('/to-do-list/items/delete-finished/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Item.objects.count(), initial_count - finished_count)
        self.assertEqual(Item.objects.filter(finished=True).count(), 0)


class RepeatingItemsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        Category.objects.create(name='Test', is_active=True)

    def test_checking_repeating_item_marks_current_finished_and_creates_next_due_from_due_date(self):
        item = Item.objects.create(
            title='Repeat From Due',
            category='Test',
            due_date=date(2026, 5, 10),
            priority=Item.IMPORTANT,
            finished=False,
            repeat=True,
            repeat_interval=3,
            repeat_unit=Item.REPEAT_DAYS,
            repeat_from_due_date=True,
            note='keep me',
        )

        response = self.client.post(f'/to-do-list/items/{item.id}/toggle-finished/', {'finished': 'on'})

        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertTrue(item.finished)

        items = list(Item.objects.filter(title='Repeat From Due').order_by('id'))
        self.assertEqual(len(items), 2)
        next_item = items[1]
        self.assertFalse(next_item.finished)
        self.assertEqual(next_item.due_date, date(2026, 5, 13))
        self.assertEqual(next_item.category, item.category)
        self.assertEqual(next_item.note, item.note)
        self.assertTrue(next_item.repeat)
        self.assertEqual(next_item.repeat_interval, 3)
        self.assertEqual(next_item.repeat_unit, Item.REPEAT_DAYS)
        self.assertTrue(next_item.repeat_from_due_date)

    def test_checking_repeating_item_creates_next_due_from_today_for_checked_mode(self):
        today = timezone.localdate()
        item = Item.objects.create(
            title='Repeat From Checked',
            category='Test',
            due_date=today - timedelta(days=10),
            priority=Item.IMPORTANT,
            finished=False,
            repeat=True,
            repeat_interval=5,
            repeat_unit=Item.REPEAT_WEEKS,
            repeat_from_due_date=False,
        )

        response = self.client.post(f'/to-do-list/items/{item.id}/toggle-finished/', {'finished': 'on'})

        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertTrue(item.finished)

        items = list(Item.objects.filter(title='Repeat From Checked').order_by('id'))
        self.assertEqual(len(items), 2)
        next_item = items[1]
        self.assertFalse(next_item.finished)
        self.assertEqual(next_item.due_date, today + timedelta(weeks=5))

    def test_checking_repeating_item_advances_by_months(self):
        item = Item.objects.create(
            title='Repeat Monthly',
            category='Test',
            due_date=date(2026, 1, 31),
            priority=Item.IMPORTANT,
            finished=False,
            repeat=True,
            repeat_interval=1,
            repeat_unit=Item.REPEAT_MONTHS,
            repeat_from_due_date=True,
        )

        response = self.client.post(f'/to-do-list/items/{item.id}/toggle-finished/', {'finished': 'on'})

        self.assertEqual(response.status_code, 302)
        next_item = Item.objects.filter(title='Repeat Monthly').order_by('id').last()
        self.assertEqual(next_item.due_date, date(2026, 2, 28))

    def test_reposting_finished_repeating_item_does_not_create_duplicate_next_item(self):
        item = Item.objects.create(
            title='Repeat Once',
            category='Test',
            due_date=date(2026, 5, 10),
            priority=Item.IMPORTANT,
            finished=False,
            repeat=True,
            repeat_interval=1,
            repeat_unit=Item.REPEAT_DAYS,
            repeat_from_due_date=True,
        )

        first_response = self.client.post(f'/to-do-list/items/{item.id}/toggle-finished/', {'finished': 'on'})
        second_response = self.client.post(f'/to-do-list/items/{item.id}/toggle-finished/', {'finished': 'on'})

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(Item.objects.filter(title='Repeat Once').count(), 2)

    def test_unchecking_repeating_item_deletes_matching_generated_next_item(self):
        item = Item.objects.create(
            title='Repeat Undo',
            category='Test',
            due_date=date(2026, 5, 10),
            priority=Item.IMPORTANT,
            finished=False,
            repeat=True,
            repeat_interval=1,
            repeat_unit=Item.REPEAT_DAYS,
            repeat_from_due_date=True,
        )

        self.client.post(f'/to-do-list/items/{item.id}/toggle-finished/', {'finished': 'on'})
        self.assertEqual(Item.objects.filter(title='Repeat Undo').count(), 2)

        response = self.client.post(f'/to-do-list/items/{item.id}/toggle-finished/')

        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertFalse(item.finished)
        self.assertEqual(Item.objects.filter(title='Repeat Undo').count(), 1)

    def test_unchecking_repeating_item_keeps_modified_generated_next_item(self):
        item = Item.objects.create(
            title='Repeat Keep Modified',
            category='Test',
            due_date=date(2026, 5, 10),
            priority=Item.IMPORTANT,
            finished=False,
            repeat=True,
            repeat_interval=1,
            repeat_unit=Item.REPEAT_DAYS,
            repeat_from_due_date=True,
        )

        self.client.post(f'/to-do-list/items/{item.id}/toggle-finished/', {'finished': 'on'})
        generated_item = Item.objects.filter(title='Repeat Keep Modified').order_by('id').last()
        generated_item.starred = True
        generated_item.save(update_fields=['starred'])

        response = self.client.post(f'/to-do-list/items/{item.id}/toggle-finished/')

        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertFalse(item.finished)
        self.assertEqual(Item.objects.filter(title='Repeat Keep Modified').count(), 2)


class CategoryEditingTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.category = Category.objects.create(name='Home', is_active=True)
        self.other_category = Category.objects.create(name='Work', is_active=False)
        Item.objects.create(
            title='Sweep',
            category='Home',
            due_date=date.today(),
            priority=Item.IMPORTANT,
            finished=False,
        )

    def test_edit_button_appears_for_real_categories(self):
        response = self.client.get('/to-do-list/')

        self.assertContains(response, 'todo-category-edit-button')
        self.assertContains(response, 'Edit')

    def test_edit_panel_opens_for_selected_category(self):
        response = self.client.get(f'/to-do-list/?edit_category={self.category.id}')

        self.assertContains(response, 'Edit category name')
        self.assertContains(response, 'Save Category')
        self.assertContains(response, 'value="Home"', html=False)

    def test_updating_category_renames_items_using_old_category_name(self):
        response = self.client.post(f'/to-do-list/categories/{self.category.id}/update/', {'name': 'House'})

        self.assertEqual(response.status_code, 302)
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, 'House')
        self.assertTrue(Item.objects.filter(category='House', title='Sweep').exists())
        self.assertFalse(Item.objects.filter(category='Home', title='Sweep').exists())

    def test_updating_category_rejects_duplicate_names(self):
        response = self.client.post(f'/to-do-list/categories/{self.category.id}/update/', {'name': 'Work'})

        self.assertEqual(response.status_code, 200)
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, 'Home')
        self.assertContains(response, 'That category already exists.')


class ItemEditingTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        Category.objects.create(name='Home', is_active=True)
        Category.objects.create(name='Work', is_active=False)
        self.item = Item.objects.create(
            title='Pay bills',
            category='Home',
            due_date=date(2026, 5, 20),
            due_time='09:30:00',
            warning_days=2,
            priority=Item.MILD,
            finished=False,
            note='electric and water',
            repeat=True,
            repeat_interval=4,
            repeat_unit=Item.REPEAT_WEEKS,
            repeat_from_due_date=False,
        )

    def test_edit_panel_opens_for_selected_item(self):
        response = self.client.get(f'/to-do-list/?edit_item={self.item.id}')

        self.assertContains(response, 'Save Item')
        self.assertContains(response, 'Delete Item')
        self.assertContains(response, 'value="Pay bills"', html=False)

    def test_updating_item_changes_all_main_fields(self):
        response = self.client.post(f'/to-do-list/items/{self.item.id}/update/', {
            'title': 'Pay utilities',
            'category': 'Work',
            'due_date': '2026-05-25',
            'due_time': '13:45',
            'warning_days': '5',
            'repeat_mode': 'due',
            'repeat_interval': '7',
            'repeat_unit': Item.REPEAT_MONTHS,
            'priority': str(Item.SEVERE),
            'note': 'updated note',
        })

        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.title, 'Pay utilities')
        self.assertEqual(self.item.category, 'Work')
        self.assertEqual(self.item.due_date.isoformat(), '2026-05-25')
        self.assertEqual(self.item.due_time.isoformat(timespec='minutes'), '13:45')
        self.assertEqual(self.item.warning_days, 5)
        self.assertEqual(self.item.priority, Item.SEVERE)
        self.assertEqual(self.item.note, 'updated note')
        self.assertTrue(self.item.repeat)
        self.assertEqual(self.item.repeat_interval, 7)
        self.assertEqual(self.item.repeat_unit, Item.REPEAT_MONTHS)
        self.assertTrue(self.item.repeat_from_due_date)

    def test_updating_item_rejects_invalid_category(self):
        response = self.client.post(f'/to-do-list/items/{self.item.id}/update/', {
            'title': 'Pay bills',
            'category': 'Missing',
            'due_date': '2026-05-20',
            'due_time': '09:30',
            'warning_days': '2',
            'repeat_mode': 'checked',
            'repeat_interval': '4',
            'repeat_unit': Item.REPEAT_WEEKS,
            'priority': str(Item.MILD),
            'note': 'electric and water',
        })

        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.category, 'Home')
        self.assertContains(response, 'Choose a valid category.')

    def test_toggling_item_starred_updates_database(self):
        self.assertFalse(self.item.starred)

        response = self.client.post(f'/to-do-list/items/{self.item.id}/toggle-starred/')

        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertTrue(self.item.starred)

from django.contrib import admin

from .models import Category, Item


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'finished',
        'due_date',
        'priority',
        'category',
        'repeat',
        'repeat_interval',
        'repeat_from_due_date',
    )
    list_filter = ('finished', 'priority', 'category', 'repeat', 'repeat_from_due_date', 'due_date')
    search_fields = ('title', 'note', 'category')

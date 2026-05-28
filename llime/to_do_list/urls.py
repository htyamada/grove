from django.urls import path

from . import views

app_name = 'to_do_list'

urlpatterns = [
    path('', views.index, name='index'),
    path('items/new/', views.new_item, name='new_item'),
    path('items/<int:item_id>/update/', views.update_item, name='update_item'),
    path('items/<int:item_id>/toggle-finished/', views.toggle_item_finished, name='toggle_item_finished'),
    path('items/<int:item_id>/toggle-starred/', views.toggle_item_starred, name='toggle_item_starred'),
    path('items/<int:item_id>/delete/', views.delete_item, name='delete_item'),
    path('items/delete-finished/', views.delete_finished_items, name='delete_finished_items'),
    path('categories/new/', views.new_category, name='new_category'),
    path('categories/<int:category_id>/update/', views.update_category, name='update_category'),
    path('categories/<int:category_id>/select/', views.select_category, name='select_category'),
    path('categories/virtual/<str:virtual_category>/select/', views.select_virtual_category, name='select_virtual_category'),
]

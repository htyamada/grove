from django.urls import path

from . import views

app_name = 'documentview'

urlpatterns = [
    path('', views.index, name='index'),
    path('browse/<path:rel_path>/', views.browse, name='browse'),
    path('exports/', views.exports_index, name='exports_index'),
    path('exports/cover/refresh/', views.exports_cover_refresh, name='exports_cover_refresh'),
    path('exports/cover/<str:name>/', views.exports_cover, name='exports_cover'),
    path('exports/preview/<str:name>/', views.exports_preview, name='exports_preview'),
    path('exports/download/<str:name>/', views.exports_download, name='exports_download'),
    path('exports/view/<str:name>/', views.exports_view, name='exports_view'),
    path('view/<path:rel_path>/', views.view, name='view'),
    path('preview/<path:rel_path>/', views.preview, name='preview'),
    path('download/<path:rel_path>/', views.download, name='download'),
    path('cover/refresh/', views.cover_refresh, name='cover_refresh'),
    path('cover/<path:rel_path>/', views.cover, name='cover'),
    path('active/add/', views.active_add, name='active_add'),
    path('active/remove/', views.active_remove, name='active_remove'),
]

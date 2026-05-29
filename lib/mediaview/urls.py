from django.urls import path
from . import views

app_name = 'mediaview'

urlpatterns = [
    path('', views.index, name='index'),
    path('browse/<str:root_name>/', views.browse, name='browse_root'),
    path('browse/<str:root_name>/<path:subpath>', views.browse, name='browse'),
    path('thumb/<str:root_name>/<path:subpath>', views.thumbnail, name='thumbnail'),
    path('large-thumb/<str:root_name>/<path:subpath>', views.large_thumbnail, name='large_thumbnail'),
    path('file/<str:root_name>/<path:subpath>', views.serve_file, name='file'),
    path('info/<str:root_name>/<path:subpath>', views.info, name='info'),
    path('dirs/<str:root_name>/', views.dirs, name='dirs'),
    path('metadata/', views.save_metadata, name='metadata'),
    path('delete/', views.delete_file, name='delete'),
    path('rename/', views.rename_file, name='rename'),
    path('move/', views.move_file, name='move'),
    path('mkdir/', views.mkdir, name='mkdir'),
]

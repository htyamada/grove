from django.urls import path
from . import views

app_name = 'image_handler'

urlpatterns = [
    path('', views.index, name='index'),
    path('browse/', views.browse, name='browse'),
    path('similarity/', views.similarity_browse, name='similarity_browse'),
    path('semantic/', views.semantic_search, name='semantic_search'),
    path('compare/', views.compare, name='compare'),
    path('cluster/<int:cluster_id>/', views.cluster_detail, name='cluster_detail'),
    path('embed-stream/', views.embed_stream, name='embed_stream'),
    path('embed-cancel/', views.embed_cancel, name='embed_cancel'),
    path('mark/', views.mark_toggle, name='mark_toggle'),
    path('deletion-list/', views.deletion_list_download, name='deletion_list_download'),
    path('deletion-list/clear/', views.deletion_list_clear, name='deletion_list_clear'),
    path('similar/', views.similar, name='similar'),
    path('thumb/', views.thumb, name='thumb'),
    path('image/', views.image, name='image'),
]

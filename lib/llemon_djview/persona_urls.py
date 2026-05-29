from django.urls import path

from . import views


app_name = 'llemon_persona'

urlpatterns = [
    path('', views.persona_index, name='index'),
    path('session/', views.persona_session, name='session'),
    path('configs/', views.persona_configs, name='configs'),
    path('chat/', views.persona_chat, name='chat'),
    path('stream/', views.persona_stream, name='stream'),
    path('render/', views.persona_render_markdown, name='render_markdown'),
    path('edit/', views.persona_edit_history, name='edit_history'),
    path('delete-history/', views.persona_delete_history, name='delete_history'),
    path('set-name/', views.persona_set_history_name, name='set_history_name'),
    path('set-title/', views.persona_set_history_title, name='set_history_title'),
    path('system/', views.persona_system, name='system'),
    path('service/', views.persona_service, name='service'),
    path('services/', views.persona_services, name='services'),
    path('models/', views.persona_models, name='models'),
]

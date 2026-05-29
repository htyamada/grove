from django.urls import path

from . import views
from .media import media_urlpatterns


app_name = 'llemon'

urlpatterns = [
    path('', views.index, name='index'),
    *media_urlpatterns(views),
]

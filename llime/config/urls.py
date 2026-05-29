"""
URL configuration for llime project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('base.urls')),
    path('llemon/', include('llemon_djview.urls')),
    path('llemon/persona/', include('llemon_djview.persona_urls')),
    path('image_handler/', include('image_handler.urls')),
    path('to-do-list/', include('to_do_list.urls')),
    path('mediaview/', include('mediaview.urls', namespace='mediaview')),
]

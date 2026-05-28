from . import views
from hty7.llemon.djview.media import media_urlpatterns

app_name = 'llemon'

urlpatterns = [
    *media_urlpatterns(views),
]

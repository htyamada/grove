from . import views
from llemon_djview.media import media_urlpatterns

app_name = 'llemon'

urlpatterns = [
    *media_urlpatterns(views),
]

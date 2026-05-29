from django.apps import AppConfig  # type: ignore[import-untyped]
from django.conf import settings  # type: ignore[import-untyped]


class ImageHandlerDjviewConfig(AppConfig):
    name = 'imhandler.djview'
    label = 'imhandler_djview'
    verbose_name = 'Image Handler Django Views'

    def ready(self):
        from imhandler import appconfig
        appconfig.init_variant(getattr(settings, 'IMHANDLER_VARIANT', 'hty7'))

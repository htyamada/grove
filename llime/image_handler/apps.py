from django.apps import AppConfig


class ImageHandlerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'image_handler'

    def ready(self):
        from hty7.imhandler import appconfig
        appconfig.init_variant('hty7')

from django.apps import AppConfig


class DocumentViewConfig(AppConfig):
    name = 'documentview'
    label = 'documentview'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import config
        config.validate_shape()

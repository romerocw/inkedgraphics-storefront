from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = 'catalog'

    def ready(self):
        from . import checks  # noqa: F401  (registers the ops API configuration checks)

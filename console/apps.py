from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    name = 'console'

    def ready(self):
        from . import signals  # noqa: F401  (registers the staff-profile signal)

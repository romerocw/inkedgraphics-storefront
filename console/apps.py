from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    name = 'console'

    def ready(self):
        from . import checks, signals  # noqa: F401  (registers the sign-in checks and the staff-profile signal)

from django.apps import AppConfig


class AssetsConfig(AppConfig):
    name = 'assets'

    def ready(self):
        import assets.signals  # noqa

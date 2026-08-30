from django.apps import AppConfig


class RecommendConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.recommend"
    verbose_name = "추천"

    def ready(self) -> None:
        from apps.recommend import checks  # noqa: F401

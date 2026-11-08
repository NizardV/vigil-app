from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from core.views import health

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="admin:index", permanent=False)),  # pas d'autre interface pour l'instant
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("projects/", include("modules.projects.urls")),
    path("watch/", include("modules.watch.urls")),
]

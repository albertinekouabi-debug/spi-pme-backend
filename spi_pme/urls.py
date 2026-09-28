from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.registry.urls")),
    path("api/v1/", include("apps.resources.urls")),
    path("api/v1/", include("apps.treasury.urls")),
    path("api/v1/", include("apps.tasks.urls")),
    path("api/v1/", include("apps.intelligence.urls")),
    path("api/v1/", include("apps.alerts.urls")),
    path("api/v1/", include("apps.imports.urls")),
    path("api/v1/", include("apps.audit.urls")),
]


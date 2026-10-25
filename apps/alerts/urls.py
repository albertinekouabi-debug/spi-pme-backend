"""Routes REST du module alertes, montées sous /api/v1/."""
from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    AlerteViewSet,
    GenererAlertesView,
    IgnorerAlerteView,
    ResumeAlertesView,
    TraiterAlerteView,
)

router = SimpleRouter()
router.register("alerts", AlerteViewSet, basename="alerte")

urlpatterns = [
    path("alerts/summary/", ResumeAlertesView.as_view(), name="alerts-summary"),
    path("alerts/generate/", GenererAlertesView.as_view(), name="alerts-generate"),
    path("alerts/<int:pk>/resolve/", TraiterAlerteView.as_view(), name="alerts-resolve"),
    path("alerts/<int:pk>/ignore/", IgnorerAlerteView.as_view(), name="alerts-ignore"),
]
urlpatterns += router.urls

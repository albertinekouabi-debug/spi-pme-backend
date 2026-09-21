from rest_framework.routers import DefaultRouter

from .views import AlerteViewSet

router = DefaultRouter()
router.register("alerts", AlerteViewSet, basename="alerte")

urlpatterns = router.urls

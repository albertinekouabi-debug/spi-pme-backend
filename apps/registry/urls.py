from rest_framework.routers import DefaultRouter

from .views import EntiteViewSet

router = DefaultRouter()
router.register("entities", EntiteViewSet, basename="entite")

urlpatterns = router.urls

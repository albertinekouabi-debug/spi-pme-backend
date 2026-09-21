from rest_framework.routers import DefaultRouter

from .views import RessourceViewSet

router = DefaultRouter()
router.register("resources", RessourceViewSet, basename="ressource")

urlpatterns = router.urls

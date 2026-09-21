from rest_framework.routers import DefaultRouter

from .views import TacheViewSet

router = DefaultRouter()
router.register("tasks", TacheViewSet, basename="tache")

urlpatterns = router.urls

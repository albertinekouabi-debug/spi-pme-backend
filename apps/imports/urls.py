from rest_framework.routers import DefaultRouter

from .views import ImportFichierViewSet

router = DefaultRouter()
router.register("imports", ImportFichierViewSet, basename="importfichier")

urlpatterns = router.urls

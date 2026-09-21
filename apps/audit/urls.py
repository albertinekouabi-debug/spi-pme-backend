from rest_framework.routers import DefaultRouter

from .views import JournalAuditViewSet

router = DefaultRouter()
router.register("audit-log", JournalAuditViewSet, basename="journalaudit")

urlpatterns = router.urls

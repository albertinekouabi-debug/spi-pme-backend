from rest_framework.routers import DefaultRouter

from .views import SuggestionViewSet

router = DefaultRouter()
router.register("suggestions", SuggestionViewSet, basename="suggestion")

urlpatterns = router.urls

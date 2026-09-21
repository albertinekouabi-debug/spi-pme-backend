from rest_framework.routers import DefaultRouter

from .views import DeclarationConformiteViewSet, FactureViewSet, TransactionViewSet

router = DefaultRouter()
router.register("transactions", TransactionViewSet, basename="transaction")
router.register("invoices", FactureViewSet, basename="facture")
router.register("compliance-declarations", DeclarationConformiteViewSet, basename="declarationconformite")

urlpatterns = router.urls

from rest_framework.routers import DefaultRouter

from .views import DeclarationConformiteViewSet, DemandeReouvertureViewSet, FactureViewSet, TransactionViewSet

router = DefaultRouter()
router.register("transactions", TransactionViewSet, basename="transaction")
router.register("reopen-requests", DemandeReouvertureViewSet, basename="demandereouverture")
router.register("invoices", FactureViewSet, basename="facture")
router.register("compliance-declarations", DeclarationConformiteViewSet, basename="declarationconformite")

urlpatterns = router.urls

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import HasRolePermission

from . import compliance
from .models import DeclarationConformite, Facture, Transaction
from .serializers import (
    DeclarationConformiteSerializer,
    DeclarerSerializer,
    ExempterSerializer,
    FactureSerializer,
    SignalementManuelSerializer,
    TransactionSerializer,
)


def _perimetre_secteurs(user):
    """Même règle que Registre/Ressources (§3.2) : Administrateur = tout, sinon secteurs rattachés."""
    if user.role and user.role.nom == "Administrateur":
        return None  # pas de restriction
    secteurs = set(user.secteurs.values_list("id", flat=True))
    if user.secteur_principal_id:
        secteurs.add(user.secteur_principal_id)
    return secteurs


class TransactionViewSet(viewsets.ModelViewSet):
    """
    /api/v1/transactions

    FR-TRE-01 : saisie des entrées/sorties.
    Un mouvement_stock met aussi à jour Ressource.niveau_actuel (voir Transaction.save()),
    ce qui recalcule automatiquement son statut (FR-STK-02).
    """

    serializer_class = TransactionSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "GET": ["treasury.read"],
        "POST": ["treasury.write"],
        "PUT": ["treasury.write"],
        "PATCH": ["treasury.write"],
        "DELETE": ["treasury.write"],
    }

    def get_queryset(self):
        user = self.request.user
        queryset = Transaction.objects.select_related("entite", "ressource", "secteur", "auteur")

        secteurs_autorises = _perimetre_secteurs(user)
        if secteurs_autorises is not None:
            queryset = queryset.filter(secteur_id__in=secteurs_autorises)

        params = self.request.query_params
        if secteur_id := params.get("secteur"):
            queryset = queryset.filter(secteur_id=secteur_id)
        if type_transaction := params.get("type"):
            queryset = queryset.filter(type=type_transaction)
        if entite_id := params.get("entite"):
            queryset = queryset.filter(entite_id=entite_id)
        if ressource_id := params.get("ressource"):
            queryset = queryset.filter(ressource_id=ressource_id)
        if date_debut := params.get("date_debut"):
            queryset = queryset.filter(date_transaction__gte=date_debut)
        if date_fin := params.get("date_fin"):
            queryset = queryset.filter(date_transaction__lte=date_fin)

        return queryset

    def _queryset_financiere(self):
        """Transactions entrée/sortie uniquement, dans le périmètre courant (hors filtres additionnels)."""
        user = self.request.user
        queryset = Transaction.objects.filter(type__in=["entree", "sortie"])
        secteurs_autorises = _perimetre_secteurs(user)
        if secteurs_autorises is not None:
            queryset = queryset.filter(secteur_id__in=secteurs_autorises)
        if secteur_id := self.request.query_params.get("secteur"):
            queryset = queryset.filter(secteur_id=secteur_id)
        return queryset

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """
        GET /api/v1/transactions/summary — cartes de la maquette Trésorerie
        (Entrées/Sorties du mois, solde net du mois, solde disponible cumulé).
        """
        queryset = self._queryset_financiere()
        maintenant = timezone.now()
        debut_mois = maintenant.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        ce_mois = queryset.filter(date_transaction__gte=debut_mois)
        entrees_mois = ce_mois.filter(type="entree").aggregate(s=Sum("montant"))["s"] or Decimal("0")
        sorties_mois = ce_mois.filter(type="sortie").aggregate(s=Sum("montant"))["s"] or Decimal("0")

        entrees_total = queryset.filter(type="entree").aggregate(s=Sum("montant"))["s"] or Decimal("0")
        sorties_total = queryset.filter(type="sortie").aggregate(s=Sum("montant"))["s"] or Decimal("0")

        return Response({
            "entrees_mois": str(entrees_mois),
            "sorties_mois": str(sorties_mois),
            "solde_net_mois": str(entrees_mois - sorties_mois),
            "solde_disponible": str(entrees_total - sorties_total),
        })

    @action(detail=False, methods=["get"], url_path="evolution")
    def evolution(self, request):
        """
        GET /api/v1/transactions/evolution?jours=7 — solde cumulé jour par jour,
        pour le graphe "Évolution du solde" de la maquette Trésorerie.
        """
        try:
            jours = max(1, min(int(request.query_params.get("jours", 7)), 90))
        except ValueError:
            jours = 7

        queryset = self._queryset_financiere()
        aujourdhui = timezone.now().date()
        points = []
        for i in range(jours - 1, -1, -1):
            jour = aujourdhui - timedelta(days=i)
            cumul = queryset.filter(date_transaction__date__lte=jour)
            entrees = cumul.filter(type="entree").aggregate(s=Sum("montant"))["s"] or Decimal("0")
            sorties = cumul.filter(type="sortie").aggregate(s=Sum("montant"))["s"] or Decimal("0")
            points.append({"date": jour.isoformat(), "solde": str(entrees - sorties)})

        return Response(points)


class FactureViewSet(viewsets.ModelViewSet):
    """/api/v1/invoices — FR-TRE-02 : suivi des factures et relances."""

    serializer_class = FactureSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "GET": ["treasury.read"],
        "POST": ["treasury.write"],
        "PUT": ["treasury.write"],
        "PATCH": ["treasury.write"],
        "DELETE": ["treasury.write"],
    }

    def get_queryset(self):
        user = self.request.user
        queryset = Facture.objects.select_related("entite", "secteur", "transaction")

        secteurs_autorises = _perimetre_secteurs(user)
        if secteurs_autorises is not None:
            queryset = queryset.filter(secteur_id__in=secteurs_autorises)

        params = self.request.query_params
        if statut := params.get("statut"):
            queryset = queryset.filter(statut=statut)
        if entite_id := params.get("entite"):
            queryset = queryset.filter(entite_id=entite_id)

        return queryset


class DeclarationConformiteViewSet(viewsets.ReadOnlyModelViewSet):
    """
    /api/v1/compliance-declarations

    Obligation légale CEMAC/COBAC (paiements en espèces au-delà du seuil
    configuré — voir apps.treasury.compliance, seuil lu depuis
    ParametreSysteme, jamais codé en dur). Lecture seule + actions
    explicites, même garantie structurelle que intelligence/alerts :
    aucune route PATCH générique sur une matière à obligation légale.
    """

    serializer_class = DeclarationConformiteSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "GET": ["treasury.read"],
        "POST": ["treasury.write"],
    }

    def get_queryset(self):
        user = self.request.user
        queryset = DeclarationConformite.objects.select_related("transaction", "declarant")

        secteurs_autorises = _perimetre_secteurs(user)
        if secteurs_autorises is not None:
            queryset = queryset.filter(transaction__secteur_id__in=secteurs_autorises)

        params = self.request.query_params
        if statut := params.get("statut"):
            queryset = queryset.filter(statut=statut)
        if motif := params.get("motif"):
            queryset = queryset.filter(motif=motif)

        return queryset

    @action(detail=False, methods=["post"], url_path="signal")
    def signal(self, request):
        """
        POST /api/v1/compliance-declarations/signal
        Signalement manuel d'une transaction jugée suspecte, quel qu'en soit
        le montant — la loi CEMAC ne limite pas cette obligation au seuil
        automatique des espèces.
        """
        serializer = SignalementManuelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            declaration = compliance.signaler_manuellement(
                serializer.validated_data["transaction"], serializer.validated_data["note"]
            )
        except compliance.DeclarationConformiteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ObjectDoesNotExist:
            return Response({"detail": "Transaction introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(DeclarationConformiteSerializer(declaration).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="declare")
    def declare(self, request, pk=None):
        serializer = DeclarerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            declaration = compliance.marquer_declaree(
                pk, declarant=request.user, reference_declaration=serializer.validated_data["reference_declaration"]
            )
        except compliance.DeclarationConformiteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ObjectDoesNotExist:
            return Response({"detail": "Déclaration introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(DeclarationConformiteSerializer(declaration).data)

    @action(detail=True, methods=["post"], url_path="exempt")
    def exempt(self, request, pk=None):
        serializer = ExempterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            declaration = compliance.marquer_exemptee(
                pk, declarant=request.user, note=serializer.validated_data["note"]
            )
        except compliance.DeclarationConformiteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ObjectDoesNotExist:
            return Response({"detail": "Déclaration introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(DeclarationConformiteSerializer(declaration).data)

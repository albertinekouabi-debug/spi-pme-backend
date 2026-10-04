from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import MethodNotAllowed
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.concurrence import ConcurrenceOptimisteMixin
from apps.core.idempotence import IdempotentCreateMixin, idempotente
from apps.accounts.permissions import HasRolePermission

from . import compliance
from . import workflow
from .models import DeclarationConformite, Facture, Transaction
from .serializers import (
    AnnulerFactureSerializer,
    AvoirSerializer,
    CorrectionTransactionSerializer,
    DeclarationConformiteSerializer,
    DeclarerSerializer,
    ExempterSerializer,
    FactureSerializer,
    MotifSerializer,
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



def _reponse_erreur_workflow(exc: "workflow.ErreurWorkflow"):
    return Response({"detail": exc.message}, status=exc.code_http)


def _request_id(request):
    return request.headers.get("X-Request-ID") or None


class TransactionViewSet(IdempotentCreateMixin, ConcurrenceOptimisteMixin, viewsets.ModelViewSet):
    """
    /api/v1/transactions

    FR-TRE-01 : saisie des entrées/sorties.
    Un mouvement_stock met aussi à jour Ressource.niveau_actuel (voir Transaction.save()),
    ce qui recalcule automatiquement son statut (FR-STK-02).

    Politique de correction (décision métier arrêtée, voir workflow.py) :
      brouillon  → PATCH et DELETE autorisés (aucun effet de stock ni de KPI) ;
      validée    → données financières IMMUABLES (409), suppression interdite (405) ;
      erreur     → POST /contre-passer (annulation) ou /corriger (annulation + remplacement) ;
      exception  → POST /reouvrir (permission treasury.reopen + motif, journalisé).
    """

    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]
    serializer_class = TransactionSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "GET": ["treasury.read"],
        "POST": ["treasury.write"],
        "PUT": ["treasury.write"],
        "PATCH": ["treasury.write"],
        "DELETE": ["treasury.write"],
    }

    def destroy(self, request, *args, **kwargs):
        transaction = self.get_object()
        if transaction.statut != "brouillon":
            raise MethodNotAllowed(
                "DELETE",
                detail="Une transaction validée ne se supprime jamais : utiliser /contre-passer ou /corriger.",
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="valider")
    @idempotente
    def valider(self, request, pk=None):
        """POST /transactions/{id}/valider — brouillon → validée (effet de stock et conformité appliqués ici)."""
        try:
            t = workflow.valider_transaction(self.get_object(), request.user, _request_id(request))
        except workflow.ErreurWorkflow as exc:
            return _reponse_erreur_workflow(exc)
        return Response(TransactionSerializer(t, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="contre-passer")
    @idempotente
    def contre_passer(self, request, pk=None):
        """POST /transactions/{id}/contre-passer {motif} — crée l'écriture inverse ; l'original est conservé."""
        serializer = MotifSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            inverse = workflow.contre_passer(
                self.get_object(), request.user, serializer.validated_data["motif"], _request_id(request)
            )
        except workflow.ErreurWorkflow as exc:
            return _reponse_erreur_workflow(exc)
        return Response(TransactionSerializer(inverse, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="corriger")
    @idempotente
    def corriger(self, request, pk=None):
        """
        POST /transactions/{id}/corriger {motif, remplacement:{...champs d'une transaction...}}
        Contre-écriture + nouvelle transaction, atomiquement. Le remplacement est validé comme une création.
        """
        entree = CorrectionTransactionSerializer(data=request.data)
        entree.is_valid(raise_exception=True)
        original = self.get_object()
        donnees = dict(entree.validated_data["remplacement"])
        donnees.pop("statut", None)
        remplacement = TransactionSerializer(data=donnees, context={"request": request})
        remplacement.is_valid(raise_exception=True)
        champs = {k: v for k, v in remplacement.validated_data.items() if k != "statut"}
        try:
            inverse, nouvelle = workflow.corriger(
                original, request.user, entree.validated_data["motif"], champs, _request_id(request)
            )
        except workflow.ErreurWorkflow as exc:
            return _reponse_erreur_workflow(exc)
        contexte = {"request": request}
        return Response({
            "contre_ecriture": TransactionSerializer(inverse, context=contexte).data,
            "remplacement": TransactionSerializer(nouvelle, context=contexte).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="reouvrir")
    @idempotente
    def reouvrir(self, request, pk=None):
        """POST /transactions/{id}/reouvrir {motif} — exceptionnel : permission treasury.reopen, motif, audit."""
        serializer = MotifSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            t = workflow.reouvrir(self.get_object(), request.user, serializer.validated_data["motif"], _request_id(request))
        except workflow.ErreurWorkflow as exc:
            return _reponse_erreur_workflow(exc)
        return Response(TransactionSerializer(t, context={"request": request}).data)

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
        if statut := params.get("statut"):
            queryset = queryset.filter(statut=statut)
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
        queryset = Transaction.objects.filter(type__in=["entree", "sortie"]).exclude(statut="brouillon")
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


class FactureViewSet(IdempotentCreateMixin, ConcurrenceOptimisteMixin, viewsets.ModelViewSet):
    """
    /api/v1/invoices — FR-TRE-02 : suivi des factures et relances.

    Pas de suppression physique (audit BE-008, même garantie structurelle
    que DeclarationConformite ci-dessous) : une facture est un document
    financier qui doit rester traçable même erronée. `annuler` remplace
    DELETE.
    """

    http_method_names = ["get", "post", "put", "patch", "head", "options"]  # pas de "delete"
    serializer_class = FactureSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "GET": ["treasury.read"],
        "POST": ["treasury.write"],
        "PUT": ["treasury.write"],
        "PATCH": ["treasury.write"],
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

    @action(detail=True, methods=["post"], url_path="annuler")
    @idempotente
    def annuler(self, request, pk=None):
        """
        POST /invoices/{id}/annuler {motif} — annulation = avoir TOTAL relié + facture « annulée ».
        Jamais de suppression. Une facture déjà annulée renvoie 409 (motif d'origine conservé).
        """
        serializer = AnnulerFactureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        facture = self.get_object()
        try:
            resultat = workflow.annuler_facture(facture, request.user, serializer.validated_data["motif"], _request_id(request))
        except workflow.ErreurWorkflow as exc:
            return _reponse_erreur_workflow(exc)
        return Response(FactureSerializer(resultat).data)

    @action(detail=True, methods=["post"], url_path="avoir")
    @idempotente
    def avoir(self, request, pk=None):
        """POST /invoices/{id}/avoir {motif, montant?} — avoir (total par défaut, ou partiel)."""
        serializer = AvoirSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            avoir = workflow.emettre_avoir(
                self.get_object(), request.user, serializer.validated_data["motif"],
                serializer.validated_data.get("montant"), _request_id(request),
            )
        except workflow.ErreurWorkflow as exc:
            return _reponse_erreur_workflow(exc)
        return Response(FactureSerializer(avoir).data, status=status.HTTP_201_CREATED)


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

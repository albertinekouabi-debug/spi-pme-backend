"""API REST des alertes du SPI PME.

Les opérations d'écriture sont explicites (generate/resolve/ignore). Le ViewSet
reste strictement en lecture seule : PATCH/PUT/DELETE génériques sont refusés.
"""
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import RolePermission
from apps.core.models import Secteur

from .models import Alerte
from . import services


class AlerteAPISerializer(serializers.ModelSerializer):
    """Contrat API stable, exposant les identifiants FK utilisés par le mobile."""
    ressource_id = serializers.IntegerField(read_only=True, allow_null=True)
    entite_id = serializers.IntegerField(read_only=True, allow_null=True)
    tache_id = serializers.IntegerField(read_only=True, allow_null=True)
    facture_id = serializers.IntegerField(read_only=True, allow_null=True)
    secteur_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Alerte
        fields = (
            "id", "type", "niveau", "titre", "description",
            "ressource", "ressource_id", "entite", "entite_id",
            "tache", "tache_id", "facture", "facture_id",
            "statut", "secteur", "secteur_id",
            "date_declenchement", "date_resolution",
        )
        read_only_fields = fields


class AlertePermission(BasePermission):
    """Contrôle les codes alerts.read / alerts.write sans ouvrir les données."""
    message = "Vous ne disposez pas de la permission requise pour les alertes."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if getattr(user, "is_superuser", False):
            return True

        code = getattr(view, "permission_code", None)
        if not code:
            code = "alerts.read" if request.method in ("GET", "HEAD", "OPTIONS") else "alerts.write"
        role_id = getattr(user, "role_id", None)
        if not role_id:
            return False
        return RolePermission.objects.filter(
            role_id=role_id,
            permission__code=code,
        ).exists()


def _secteur_ids_accessibles(user):
    """Détermine les secteurs autorisés; refuse par défaut si aucun n'est connu."""
    if getattr(user, "is_superuser", False):
        return None  # None signifie sans restriction pour un superutilisateur.

    ids = set()
    principal_id = getattr(user, "secteur_principal_id", None)
    if principal_id:
        ids.add(principal_id)

    # Prend aussi en charge les éventuelles relations multi-secteurs, sans exiger
    # qu'elles existent dans toutes les variantes du modèle Utilisateur.
    for attribute in ("secteurs", "secteurs_accessibles", "secteurs_autorises"):
        try:
            relation = getattr(user, attribute, None)
            if relation is not None and hasattr(relation, "values_list"):
                ids.update(relation.values_list("pk", flat=True))
        except (AttributeError, TypeError, ValueError):
            continue
    return ids


def _alertes_accessibles(user):
    queryset = Alerte.objects.select_related(
        "ressource", "entite", "tache", "facture", "secteur"
    )
    secteur_ids = _secteur_ids_accessibles(user)
    if secteur_ids is None:
        return queryset
    if not secteur_ids:
        return queryset.none()
    return queryset.filter(secteur_id__in=secteur_ids)


def _secteur_accessible(user, secteur_id):
    secteur_ids = _secteur_ids_accessibles(user)
    secteurs = Secteur.objects.all()
    if secteur_ids is not None:
        if not secteur_ids:
            return None
        secteurs = secteurs.filter(pk__in=secteur_ids)
    return secteurs.filter(pk=secteur_id).first()


class AlerteViewSet(viewsets.ReadOnlyModelViewSet):
    """Liste et détail des alertes accessibles à l'utilisateur connecté."""
    serializer_class = AlerteAPISerializer
    permission_classes = (IsAuthenticated, AlertePermission)

    def get_queryset(self):
        return _alertes_accessibles(self.request.user)


class ResumeAlertesView(APIView):
    permission_classes = (IsAuthenticated, AlertePermission)
    permission_code = "alerts.read"

    def get(self, request):
        actives = _alertes_accessibles(request.user).filter(statut="active")
        par_niveau = {
            row["niveau"]: row["total"]
            for row in actives.values("niveau").annotate(total=Count("id"))
        }
        total_actives = sum(par_niveau.values())
        traitees = _alertes_accessibles(request.user).filter(statut="traitee").count()
        return Response({
            "critiques": par_niveau.get("critique", 0),
            "elevees": par_niveau.get("elevee", 0),
            "moderees": par_niveau.get("moderee", 0),
            "actives": total_actives,
            "total_actives": total_actives,
            "traitees": traitees,
            "resolues": traitees,
            "ignorees": _alertes_accessibles(request.user).filter(statut="ignoree").count(),
        })


class GenererAlertesView(APIView):
    permission_classes = (IsAuthenticated, AlertePermission)
    permission_code = "alerts.write"

    def post(self, request):
        raw_id = request.data.get("secteur")
        try:
            secteur_id = int(raw_id)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Le champ 'secteur' doit contenir un identifiant entier."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        secteur = _secteur_accessible(request.user, secteur_id)
        if secteur is None:
            # Ne révèle pas si un secteur inaccessible existe réellement.
            return Response({"detail": "Secteur introuvable ou non autorisé."}, status=status.HTTP_404_NOT_FOUND)

        resultat = services.generer_pour_secteur(secteur)
        return Response({
            "creees": AlerteAPISerializer(resultat["creees"], many=True).data,
            "resolues_automatiquement": AlerteAPISerializer(
                resultat["resolues_automatiquement"], many=True
            ).data,
        }, status=status.HTTP_201_CREATED)


class DecisionAlerteView(APIView):
    permission_classes = (IsAuthenticated, AlertePermission)
    permission_code = "alerts.write"
    action_service = None

    def post(self, request, pk):
        alerte = get_object_or_404(_alertes_accessibles(request.user), pk=pk)
        try:
            resultat = self.action_service(alerte.pk, utilisateur=request.user)
        except services.DecisionAlerteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except Alerte.DoesNotExist:
            return Response({"detail": "Alerte introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(AlerteAPISerializer(resultat).data, status=status.HTTP_200_OK)


class TraiterAlerteView(DecisionAlerteView):
    action_service = staticmethod(services.traiter_alerte)


class IgnorerAlerteView(DecisionAlerteView):
    action_service = staticmethod(services.ignorer_alerte)

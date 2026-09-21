from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import HasRolePermission
from apps.core.models import Secteur

from . import services
from .models import Alerte
from .serializers import AlerteSerializer, GenererAlertesSerializer


def _perimetre_secteurs(user):
    if user.role and user.role.nom == "Administrateur":
        return None
    secteurs = set(user.secteurs.values_list("id", flat=True))
    if user.secteur_principal_id:
        secteurs.add(user.secteur_principal_id)
    return secteurs


class AlerteViewSet(viewsets.ReadOnlyModelViewSet):
    """
    /api/v1/alerts — lecture seule ; les seules transitions de statut passent
    par /resolve et /ignore (pas de PATCH générique), sur le même principe de
    garantie structurelle que apps.intelligence.
    """

    serializer_class = AlerteSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "GET": ["alerts.read"],
        "POST": ["alerts.write"],
    }

    def get_queryset(self):
        user = self.request.user
        queryset = Alerte.objects.select_related("ressource", "entite", "tache", "facture", "secteur")

        secteurs_autorises = _perimetre_secteurs(user)
        if secteurs_autorises is not None:
            queryset = queryset.filter(secteur_id__in=secteurs_autorises)

        params = self.request.query_params
        if secteur_id := params.get("secteur"):
            queryset = queryset.filter(secteur_id=secteur_id)
        if statut := params.get("statut"):
            queryset = queryset.filter(statut=statut)
        if niveau := params.get("niveau"):
            queryset = queryset.filter(niveau=niveau)
        if type_alerte := params.get("type"):
            queryset = queryset.filter(type=type_alerte)

        return queryset

    def _secteur_cible(self, secteur_id):
        secteurs_autorises = _perimetre_secteurs(self.request.user)
        if secteur_id is None:
            if secteurs_autorises is None:
                raise ValidationError({"secteur": "Précisez un secteur (l'Administrateur n'a pas de secteur implicite)."})
            if len(secteurs_autorises) != 1:
                raise ValidationError({"secteur": "Précisez un secteur (vous en avez plusieurs)."})
            return Secteur.objects.get(pk=next(iter(secteurs_autorises)))

        if secteurs_autorises is not None and secteur_id not in secteurs_autorises:
            raise PermissionDenied("Ce secteur ne fait pas partie de votre périmètre.")
        try:
            return Secteur.objects.get(pk=secteur_id)
        except Secteur.DoesNotExist:
            raise ValidationError({"secteur": "Secteur introuvable."})

    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        """POST /api/v1/alerts/generate — exécute les détecteurs pour un secteur (création + résolution auto)."""
        serializer = GenererAlertesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        secteur = self._secteur_cible(serializer.validated_data.get("secteur"))

        resultat = services.generer_pour_secteur(secteur)
        return Response({
            "creees": AlerteSerializer(resultat["creees"], many=True).data,
            "resolues_automatiquement": AlerteSerializer(resultat["resolues_automatiquement"], many=True).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve(self, request, pk=None):
        """POST /api/v1/alerts/{id}/resolve — marque l'alerte comme traitée manuellement."""
        try:
            alerte = services.traiter_alerte(pk, utilisateur=request.user)
        except services.DecisionAlerteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ObjectDoesNotExist:
            return Response({"detail": "Alerte introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(AlerteSerializer(alerte).data)

    @action(detail=True, methods=["post"], url_path="ignore")
    def ignore(self, request, pk=None):
        """POST /api/v1/alerts/{id}/ignore."""
        try:
            alerte = services.ignorer_alerte(pk, utilisateur=request.user)
        except services.DecisionAlerteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ObjectDoesNotExist:
            return Response({"detail": "Alerte introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(AlerteSerializer(alerte).data)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """GET /api/v1/alerts/summary — cartes Critique/Élevée/Modérée/Résolue de la maquette Alertes."""
        queryset = self.filter_queryset(self.get_queryset())
        return Response(queryset.aggregate(
            critiques=Count("id", filter=Q(statut="active", niveau="critique")),
            elevees=Count("id", filter=Q(statut="active", niveau="elevee")),
            moderees=Count("id", filter=Q(statut="active", niveau="moderee")),
            resolues=Count("id", filter=Q(statut="traitee")),
        ))

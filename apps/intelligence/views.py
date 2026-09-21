from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import HasRolePermission
from apps.core.models import Secteur

from . import services
from .models import Suggestion
from .serializers import GenererSuggestionsSerializer, RejetSuggestionSerializer, SuggestionSerializer


def _perimetre_secteurs(user):
    if user.role and user.role.nom == "Administrateur":
        return None
    secteurs = set(user.secteurs.values_list("id", flat=True))
    if user.secteur_principal_id:
        secteurs.add(user.secteur_principal_id)
    return secteurs


class SuggestionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    /api/v1/suggestions

    Volontairement un ReadOnlyModelViewSet : il n'existe aucune route PATCH/PUT
    générique sur ce modèle. La seule manière de faire changer le statut d'une
    Suggestion est /validate ou /reject ci-dessous — c'est la garantie
    structurelle de FR-SUG-03 (aucune exécution automatique, décision humaine
    explicite et traçable).
    """

    serializer_class = SuggestionSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "GET": ["intelligence.read"],
        "POST": ["intelligence.write"],
    }

    def get_queryset(self):
        user = self.request.user
        queryset = Suggestion.objects.select_related("ressource_liee", "entite_liee", "secteur", "decideur")

        secteurs_autorises = _perimetre_secteurs(user)
        if secteurs_autorises is not None:
            queryset = queryset.filter(secteur_id__in=secteurs_autorises)

        params = self.request.query_params
        if secteur_id := params.get("secteur"):
            queryset = queryset.filter(secteur_id=secteur_id)
        if statut := params.get("statut"):
            queryset = queryset.filter(statut=statut)
        if type_algorithme := params.get("type_algorithme"):
            queryset = queryset.filter(type_algorithme=type_algorithme)
        if categorie := params.get("categorie"):
            queryset = queryset.filter(categorie__iexact=categorie)

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
        """POST /api/v1/suggestions/generate — déclenche le moteur d'algorithmes pour un secteur."""
        serializer = GenererSuggestionsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        secteur = self._secteur_cible(serializer.validated_data.get("secteur"))

        suggestions = services.generer_pour_secteur(secteur)
        return Response(
            SuggestionSerializer(suggestions, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="validate")
    def validate(self, request, pk=None):
        """POST /api/v1/suggestions/{id}/validate — FR-SUG-03 : seule voie d'exécution réelle."""
        try:
            suggestion = services.valider_suggestion(pk, decideur=request.user)
        except services.DecisionSuggestionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ObjectDoesNotExist:
            return Response({"detail": "Suggestion introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(SuggestionSerializer(suggestion).data)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        """POST /api/v1/suggestions/{id}/reject — motif obligatoire (§11.3, apprentissage)."""
        serializer = RejetSuggestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            suggestion = services.rejeter_suggestion(
                pk, decideur=request.user, motif=serializer.validated_data["motif"]
            )
        except services.DecisionSuggestionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ObjectDoesNotExist:
            return Response({"detail": "Suggestion introuvable."}, status=status.HTTP_404_NOT_FOUND)
        return Response(SuggestionSerializer(suggestion).data)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """GET /api/v1/suggestions/summary — cartes de la maquette Suggestions IA."""
        queryset = self.filter_queryset(self.get_queryset())
        maintenant = timezone.now()
        debut_mois = maintenant.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        ce_mois = queryset.filter(date_creation__gte=debut_mois)

        repartition = ce_mois.aggregate(
            total=Count("id"),
            validees=Count("id", filter=Q(statut="validee")),
            en_attente=Count("id", filter=Q(statut="en_attente")),
            rejetees=Count("id", filter=Q(statut="rejetee")),
        )
        decidees = repartition["validees"] + repartition["rejetees"]
        taux_acceptation = round(repartition["validees"] / decidees * 100, 1) if decidees else None
        repartition["taux_acceptation"] = taux_acceptation
        return Response(repartition)

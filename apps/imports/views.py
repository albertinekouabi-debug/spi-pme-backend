from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from apps.accounts.permissions import HasRolePermission
from apps.core.models import Secteur

from . import services
from .models import ImportFichier
from .serializers import ImportFichierSerializer, ImportUploadSerializer


def _perimetre_secteurs(user):
    if user.role and user.role.nom == "Administrateur":
        return None
    secteurs = set(user.secteurs.values_list("id", flat=True))
    if user.secteur_principal_id:
        secteurs.add(user.secteur_principal_id)
    return secteurs


class ImportFichierViewSet(viewsets.ReadOnlyModelViewSet):
    """
    /api/v1/imports — historique en lecture seule (FR-IMP-*).
    L'écriture ne passe que par /preview (aucune persistance) et /commit
    (parse + valide + importe), jamais par un POST générique sur la collection.
    """

    serializer_class = ImportFichierSerializer
    permission_classes = [HasRolePermission]
    parser_classes = [MultiPartParser]
    required_permissions = {
        "GET": ["imports.read"],
        "POST": ["imports.write"],
    }

    def get_queryset(self):
        user = self.request.user
        queryset = ImportFichier.objects.select_related("auteur", "secteur")
        secteurs_autorises = _perimetre_secteurs(user)
        if secteurs_autorises is not None:
            queryset = queryset.filter(secteur_id__in=secteurs_autorises)
        if secteur_id := self.request.query_params.get("secteur"):
            queryset = queryset.filter(secteur_id=secteur_id)
        if statut := self.request.query_params.get("statut"):
            queryset = queryset.filter(statut=statut)
        return queryset

    def _secteur_cible(self, secteur_id):
        secteurs_autorises = _perimetre_secteurs(self.request.user)
        if secteurs_autorises is not None and secteur_id not in secteurs_autorises:
            raise PermissionDenied("Ce secteur ne fait pas partie de votre périmètre.")
        try:
            return Secteur.objects.get(pk=secteur_id)
        except Secteur.DoesNotExist:
            raise ValidationError({"secteur": "Secteur introuvable."})

    @action(detail=False, methods=["post"], url_path="preview")
    def preview(self, request):
        """
        POST /api/v1/imports/preview (multipart: fichier, secteur)
        Étapes "Valider"/"Aperçu" de la maquette — aucune écriture en base.
        """
        serializer = ImportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fichier = serializer.validated_data["fichier"]
        self._secteur_cible(serializer.validated_data["secteur"])  # valide le périmètre, résultat non utilisé ici

        try:
            resultat = services.previsualiser(fichier.name, fichier.read())
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(resultat)

    @action(detail=False, methods=["post"], url_path="commit")
    def commit(self, request):
        """
        POST /api/v1/imports/commit (multipart: fichier, secteur)
        Étapes "Importer"/"Terminé" — persiste l'historique ET les ressources
        des lignes valides (import partiel possible, voir services.py).
        """
        serializer = ImportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fichier = serializer.validated_data["fichier"]
        secteur = self._secteur_cible(serializer.validated_data["secteur"])

        try:
            import_fichier = services.importer(fichier.name, fichier.read(), secteur, request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ImportFichierSerializer(import_fichier).data, status=status.HTTP_201_CREATED)

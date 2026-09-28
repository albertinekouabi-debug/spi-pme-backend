from rest_framework import generics, permissions

from .models import Secteur
from .serializers import SecteurSerializer


class MesSecteursView(generics.ListAPIView):
    """
    GET /api/v1/secteurs â€” secteurs accessibles Ã  l'utilisateur connectÃ©
    (secteur_principal + secteurs secondaires), utilisÃ© pour peupler le
    sÃ©lecteur "Secteur actif". Ne retourne JAMAIS tous les secteurs du
    systÃ¨me ici â€” c'est un endpoint utilisateur, pas un outil de gestion.
    Le CRUD des secteurs eux-mÃªmes (Administration) est un chantier sÃ©parÃ©,
    non couvert par cet endpoint.
    """

    serializer_class = SecteurSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        ids_accessibles = set(user.secteurs.values_list("id", flat=True))
        if user.secteur_principal_id:
            ids_accessibles.add(user.secteur_principal_id)
        return Secteur.objects.filter(id__in=ids_accessibles, actif=True).order_by("nom")


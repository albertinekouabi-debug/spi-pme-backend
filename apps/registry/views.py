from django.db.models import Count, Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import HasRolePermission

from .models import Entite
from .serializers import EntiteSerializer


class EntiteViewSet(viewsets.ModelViewSet):
    """
    /api/v1/entities

    FR-REG-01 : création et mise à jour des fiches Entité.
    FR-REG-02 : champs dynamiques adaptés au secteur (validation côté serializer).
    FR-REG-03 : recherche par nom, identifiant ou catégorie (paramètre ?q=).

    Périmètre RBAC (§3.2) : un Administrateur voit toutes les entités ; tout
    autre rôle ne voit que celles des secteurs auxquels il est rattaché
    (secteur_principal + secteurs secondaires).
    """

    serializer_class = EntiteSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "GET": ["registry.read"],
        "POST": ["registry.write"],
        "PUT": ["registry.write"],
        "PATCH": ["registry.write"],
        "DELETE": ["registry.write"],
    }

    def get_queryset(self):
        user = self.request.user
        queryset = Entite.objects.select_related("secteur", "cree_par")

        if user.role and user.role.nom != "Administrateur":
            secteurs_autorises = set(user.secteurs.values_list("id", flat=True))
            if user.secteur_principal_id:
                secteurs_autorises.add(user.secteur_principal_id)
            queryset = queryset.filter(secteur_id__in=secteurs_autorises)

        params = self.request.query_params
        secteur_id = params.get("secteur")
        if secteur_id:
            queryset = queryset.filter(secteur_id=secteur_id)

        type_entite = params.get("type")
        if type_entite:
            queryset = queryset.filter(type__iexact=type_entite)

        statut = params.get("statut")
        if statut:
            queryset = queryset.filter(statut=statut)

        recherche = params.get("q")
        if recherche:
            filtre = Q(nom__icontains=recherche) | Q(type__icontains=recherche)
            if recherche.isdigit():
                filtre |= Q(id=int(recherche))
            queryset = queryset.filter(filtre)

        return queryset

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """
        GET /api/v1/entities/summary — cartes de la maquette Registre.

        Entite.type est un champ libre configurable par secteur (§10.2), donc
        pas d'énumération fixe universelle à agréger. On regroupe simplement
        par valeur de `type` réellement présente dans le périmètre courant —
        ça reste correct quel que soit le secteur (Commerce : client/
        fournisseur/partenaire ; Santé : patient/praticien... etc.), sans
        halluciner une liste de catégories qui n'existe dans aucune table.
        """
        queryset = self.filter_queryset(self.get_queryset())
        repartition_par_type = list(
            queryset.values("type").annotate(total=Count("id")).order_by("-total")
        )
        return Response({
            "total": queryset.count(),
            "actives": queryset.filter(statut="actif").count(),
            "par_type": repartition_par_type,  # [{"type": "client", "total": 124}, ...]
        })

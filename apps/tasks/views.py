from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import HasRolePermission

from .models import Tache, TacheHistoriqueStatut
from .serializers import TacheHistoriqueStatutSerializer, TacheSerializer


def _perimetre_secteurs(user):
    if user.role and user.role.nom == "Administrateur":
        return None
    secteurs = set(user.secteurs.values_list("id", flat=True))
    if user.secteur_principal_id:
        secteurs.add(user.secteur_principal_id)
    return secteurs


class TacheViewSet(viewsets.ModelViewSet):
    """
    /api/v1/tasks

    FR-TSK-01 : CRUD tâches, assignation.
    FR-TSK-02 : historique de changement de statut (voir Tache.save()).
    FR-TSK-03 : tâches "en retard" = non terminées + échéance dépassée.
    """

    serializer_class = TacheSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "GET": ["tasks.read"],
        "POST": ["tasks.write"],
        "PUT": ["tasks.write"],
        "PATCH": ["tasks.write"],
        "DELETE": ["tasks.write"],
    }

    def get_queryset(self):
        user = self.request.user
        queryset = Tache.objects.select_related("assignee", "createur", "secteur")

        secteurs_autorises = _perimetre_secteurs(user)
        if secteurs_autorises is not None:
            queryset = queryset.filter(secteur_id__in=secteurs_autorises)

        params = self.request.query_params
        if secteur_id := params.get("secteur"):
            queryset = queryset.filter(secteur_id=secteur_id)
        if statut := params.get("statut"):
            queryset = queryset.filter(statut=statut)
        if priorite := params.get("priorite"):
            queryset = queryset.filter(priorite=priorite)
        if assignee_id := params.get("assignee"):
            queryset = queryset.filter(assignee_id=assignee_id)
        if categorie := params.get("categorie"):
            queryset = queryset.filter(categorie__iexact=categorie)
        if params.get("retard") == "true":
            queryset = queryset.filter(
                statut__in=["a_faire", "en_cours"], echeance__lt=timezone.now().date()
            )
        if recherche := params.get("q"):
            queryset = queryset.filter(Q(titre__icontains=recherche) | Q(description__icontains=recherche))

        return queryset

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """GET /api/v1/tasks/summary — cartes Toutes/Terminées/En cours/En retard de la maquette."""
        queryset = self.filter_queryset(self.get_queryset())
        aujourdhui = timezone.now().date()
        total = queryset.count()
        terminees = queryset.filter(statut="terminee").count()
        en_cours = queryset.filter(statut="en_cours").count()
        en_retard = queryset.filter(statut__in=["a_faire", "en_cours"], echeance__lt=aujourdhui).count()
        return Response({"total": total, "terminees": terminees, "en_cours": en_cours, "en_retard": en_retard})

    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, pk=None):
        """GET /api/v1/tasks/{id}/history — historique complet des changements de statut."""
        tache = self.get_object()
        historique = tache.historique_statuts.select_related("auteur").all()
        return Response(TacheHistoriqueStatutSerializer(historique, many=True).data)

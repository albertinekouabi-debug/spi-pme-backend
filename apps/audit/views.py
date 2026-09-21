from django.db.models import Count, Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import HasRolePermission

from .models import JournalAudit
from .serializers import JournalAuditSerializer


class JournalAuditViewSet(viewsets.ReadOnlyModelViewSet):
    """
    /api/v1/audit-log

    FR-AUD-01/02/03 : consultation en lecture seule, filtrable par module,
    utilisateur, résultat et période. Pas de périmètre sectoriel comme les
    autres modules : l'accès est déjà restreint par le RBAC (`audit.read`
    n'est attribué qu'aux rôles Administrateur et Auditeur par seed_rbac,
    §3.1 — un Gérant ou un Employé n'a pas cette permission), et un journal
    d'audit partitionné par secteur serait contraire à son objectif
    (traçabilité globale de l'instance).
    """

    serializer_class = JournalAuditSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {"GET": ["audit.read"]}

    def get_queryset(self):
        queryset = JournalAudit.objects.select_related("auteur")
        params = self.request.query_params

        if module := params.get("module"):
            queryset = queryset.filter(module=module)
        if resultat := params.get("resultat"):
            queryset = queryset.filter(resultat=resultat)
        if auteur_id := params.get("auteur"):
            queryset = queryset.filter(auteur_id=auteur_id)
        if action_ := params.get("action"):
            queryset = queryset.filter(action__icontains=action_)
        if recherche := params.get("q"):
            queryset = queryset.filter(
                Q(action__icontains=recherche) | Q(cible_type__icontains=recherche) | Q(cible_id__icontains=recherche)
            )
        if date_debut := params.get("date_debut"):
            queryset = queryset.filter(date_action__gte=date_debut)
        if date_fin := params.get("date_fin"):
            queryset = queryset.filter(date_action__lte=date_fin)

        return queryset

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """GET /api/v1/audit-log/summary — cartes de la maquette Journal d'audit."""
        queryset = self.filter_queryset(self.get_queryset())
        return Response(queryset.aggregate(
            total=Count("id"),
            reussies=Count("id", filter=Q(resultat="reussi")),
            avertissements=Count("id", filter=Q(resultat="avertissement")),
            echecs=Count("id", filter=Q(resultat="echec")),
        ))

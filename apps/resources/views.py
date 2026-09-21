from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import HasRolePermission

from .models import Ressource
from .serializers import RessourceSerializer


class RessourceViewSet(viewsets.ModelViewSet):
    """
    /api/v1/resources

    FR-STK-01 : CRUD Ressource.
    FR-STK-02 : statut calculé automatiquement (critique/à surveiller/stable).
    FR-STK-03 : recherche et filtres (statut, type, secteur, nom).

    Même périmètre RBAC sectoriel que le Registre (§3.2).
    """

    serializer_class = RessourceSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "GET": ["resources.read"],
        "POST": ["resources.write"],
        "PUT": ["resources.write"],
        "PATCH": ["resources.write"],
        "DELETE": ["resources.write"],
    }

    def get_queryset(self):
        user = self.request.user
        queryset = Ressource.objects.select_related("secteur", "entite", "cree_par")

        if user.role and user.role.nom != "Administrateur":
            secteurs_autorises = set(user.secteurs.values_list("id", flat=True))
            if user.secteur_principal_id:
                secteurs_autorises.add(user.secteur_principal_id)
            queryset = queryset.filter(secteur_id__in=secteurs_autorises)

        params = self.request.query_params
        if secteur_id := params.get("secteur"):
            queryset = queryset.filter(secteur_id=secteur_id)
        if statut := params.get("statut"):
            queryset = queryset.filter(statut=statut)
        if type_ressource := params.get("type"):
            queryset = queryset.filter(type__iexact=type_ressource)
        if recherche := params.get("q"):
            queryset = queryset.filter(Q(nom__icontains=recherche) | Q(type__icontains=recherche))

        return queryset

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """
        GET /api/v1/resources/summary — alimente les 4 cartes de la maquette
        Ressources (Critiques / À surveiller / Stables / Total).
        """
        queryset = self.filter_queryset(self.get_queryset())
        repartition = queryset.aggregate(
            total=Count("id"),
            critiques=Count("id", filter=Q(statut="critique")),
            a_surveiller=Count("id", filter=Q(statut="a_surveiller")),
            stables=Count("id", filter=Q(statut="stable")),
        )
        return Response(repartition)

    @action(detail=True, methods=["get"], url_path="evolution")
    def evolution(self, request, pk=None):
        """
        GET /api/v1/resources/{id}/evolution?jours=7 — "Évolution des niveaux
        de stock" de la maquette Ressources. Reconstitue le niveau historique
        à partir des Transaction de type mouvement_stock liées à cette
        ressource (le niveau_actuel courant sert de point de départ, la
        variation de chaque jour est retirée en remontant dans le temps).
        """
        from apps.treasury.models import Transaction  # import différé : treasury dépend de resources, pas l'inverse

        ressource = self.get_object()
        try:
            jours = max(1, min(int(request.query_params.get("jours", 7)), 90))
        except ValueError:
            jours = 7

        aujourdhui = timezone.now().date()
        mouvements = list(
            Transaction.objects.filter(ressource=ressource, type="mouvement_stock")
            .filter(date_transaction__date__gte=aujourdhui - timedelta(days=jours - 1))
            .order_by("-date_transaction")
        )

        niveau_courant = ressource.niveau_actuel
        niveaux_par_jour = {}
        curseur = niveau_courant
        for i in range(jours):
            jour = aujourdhui - timedelta(days=i)
            niveaux_par_jour[jour] = curseur
            variation_du_jour = sum(m.quantite for m in mouvements if m.date_transaction.date() == jour)
            curseur = curseur - variation_du_jour

        points = [{"date": jour.isoformat(), "niveau": str(niveaux_par_jour[jour])} for jour in sorted(niveaux_par_jour)]
        return Response(points)

    @action(detail=True, methods=["get"], url_path="seuils-recommandes")
    def seuils_recommandes(self, request, pk=None):
        """
        GET /api/v1/resources/{id}/seuils-recommandes?delai_jours=X&facteur_securite=Y

        Calcule une suggestion de seuils à partir de la consommation RÉELLE de
        cette ressource (mouvements de stock des 30 derniers jours) — aucune
        valeur "standard par secteur" n'est codée en dur, un hôpital et une
        épicerie n'ayant rien de comparable en la matière.

        Formule (stock de sécurité, pratique standard de gestion des stocks) :
          seuil_critique = consommation_moyenne_quotidienne × délai_jours
          seuil_alerte    = seuil_critique × facteur_securite

        `delai_jours` (délai de réapprovisionnement estimé par l'utilisateur
        auprès de ses fournisseurs) est OBLIGATOIRE : le système ne le connaît
        pas et ne doit pas le deviner. `facteur_securite` est optionnel
        (défaut 1.5, une marge de sécurité usuelle en gestion de stock),
        surchargeable par l'utilisateur.
        """
        from apps.treasury.models import Transaction  # import différé, cf. evolution() ci-dessus

        ressource = self.get_object()

        delai_brut = request.query_params.get("delai_jours")
        if not delai_brut:
            raise ValidationError({
                "delai_jours": (
                    "Obligatoire : le délai de réapprovisionnement (en jours) auprès de vos "
                    "fournisseurs n'est pas une donnée que le système peut deviner."
                )
            })
        try:
            delai_jours = Decimal(delai_brut)
            if delai_jours <= 0:
                raise ValueError
        except (ValueError, ArithmeticError):
            raise ValidationError({"delai_jours": "Doit être un nombre de jours strictement positif."})

        try:
            facteur_securite = Decimal(request.query_params.get("facteur_securite", "1.5"))
            if facteur_securite < 1:
                raise ValueError
        except (ValueError, ArithmeticError):
            raise ValidationError({"facteur_securite": "Doit être un nombre supérieur ou égal à 1."})

        fenetre_jours = 30
        depuis = timezone.now() - timedelta(days=fenetre_jours)
        consommation_totale = Transaction.objects.filter(
            ressource=ressource, type="mouvement_stock", quantite__lt=0, date_transaction__gte=depuis,
        ).aggregate(s=Sum("quantite"))["s"]

        if not consommation_totale:
            return Response({
                "calculable": False,
                "raison": (
                    "Aucun mouvement de stock (sortie) enregistré sur les 30 derniers jours pour "
                    "cette ressource — pas assez de données réelles pour une recommandation fiable."
                ),
            })

        consommation_moyenne_quotidienne = abs(consommation_totale) / Decimal(fenetre_jours)
        seuil_critique_suggere = (consommation_moyenne_quotidienne * delai_jours).quantize(Decimal("0.01"))
        seuil_alerte_suggere = (seuil_critique_suggere * facteur_securite).quantize(Decimal("0.01"))

        return Response({
            "calculable": True,
            "consommation_moyenne_quotidienne": str(consommation_moyenne_quotidienne.quantize(Decimal("0.01"))),
            "fenetre_analysee_jours": fenetre_jours,
            "delai_jours_utilise": str(delai_jours),
            "facteur_securite_utilise": str(facteur_securite),
            "seuil_critique_suggere": str(seuil_critique_suggere),
            "seuil_alerte_suggere": str(seuil_alerte_suggere),
            "note": "Suggestion basée sur la consommation réelle enregistrée — à valider avant application.",
        })

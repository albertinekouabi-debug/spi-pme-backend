"""
Algorithme "regression_lineaire" : détecte une tendance de consommation
soutenue sur les mouvements de stock récents et projette une date de rupture.

Performance : une seule requête agrégée (GROUP BY ressource, jour) pour tout
le secteur, plutôt qu'une requête par ressource — l'ajustement de régression
lui-même est vectorisé avec numpy.polyfit (pas de boucle Python sur les points).
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

import numpy as np
from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.resources.models import Ressource
from apps.treasury.models import Transaction

from .base import Algorithme, SuggestionDraft

FENETRE_JOURS = 14
SEUIL_JOURS_AVANT_RUPTURE = 7
POINTS_MINIMUM = 3


class TendanceAlgorithme(Algorithme):
    code = "regression_lineaire"

    def generer(self, secteur) -> list[SuggestionDraft]:
        aujourdhui = timezone.now().date()
        date_limite = aujourdhui - timedelta(days=FENETRE_JOURS)

        # Une seule requête agrégée pour toutes les ressources du secteur.
        mouvements = (
            Transaction.objects
            .filter(secteur=secteur, type="mouvement_stock", ressource__isnull=False,
                    date_transaction__date__gte=date_limite)
            .annotate(jour=TruncDate("date_transaction"))
            .values("ressource_id", "jour")
            .annotate(total=Sum("quantite"))
        )

        par_ressource = defaultdict(dict)
        for ligne in mouvements:
            par_ressource[ligne["ressource_id"]][ligne["jour"]] = float(ligne["total"])

        if not par_ressource:
            return []

        ressources = {
            r.id: r for r in Ressource.objects.filter(id__in=par_ressource.keys(), secteur=secteur)
        }

        drafts = []
        for ressource_id, points_par_jour in par_ressource.items():
            ressource = ressources.get(ressource_id)
            if ressource is None or len(points_par_jour) < POINTS_MINIMUM:
                continue

            draft = self._analyser_ressource(ressource, points_par_jour)
            if draft is not None:
                drafts.append(draft)

        return drafts

    def _analyser_ressource(self, ressource, points_par_jour: dict) -> SuggestionDraft | None:
        jours_tries = sorted(points_par_jour.keys())
        jour_zero = jours_tries[0]

        x = np.array([(j - jour_zero).days for j in jours_tries], dtype=float)
        variations = np.array([points_par_jour[j] for j in jours_tries], dtype=float)
        cumul = np.cumsum(variations)  # trajectoire relative du niveau sur la fenêtre

        pente, ordonnee = np.polyfit(x, cumul, 1)
        if pente >= 0:
            return None  # tendance stable ou à la hausse : pas d'alerte à générer

        prediction = pente * x + ordonnee
        ss_res = float(np.sum((cumul - prediction) ** 2))
        ss_tot = float(np.sum((cumul - np.mean(cumul)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

        jours_avant_rupture = float(ressource.niveau_actuel) / abs(pente) if pente != 0 else None
        if jours_avant_rupture is None or jours_avant_rupture > SEUIL_JOURS_AVANT_RUPTURE:
            return None

        confiance = max(Decimal("50"), min(Decimal("95"), Decimal(str(round(r2 * 100, 2)))))

        return SuggestionDraft(
            titre=f"Anticiper la rupture de {ressource.nom}",
            description=(
                f"Tendance de consommation en baisse constante sur {len(jours_tries)} jour(s) observé(s) ; "
                f"rupture projetée dans environ {jours_avant_rupture:.1f} jour(s) au rythme actuel."
            ),
            facteurs={
                "pente_quotidienne": round(pente, 3),
                "coefficient_ajustement_r2": round(r2, 3),
                "jours_avant_rupture_estimes": round(jours_avant_rupture, 1),
                "fenetre_analysee_jours": FENETRE_JOURS,
            },
            confiance=confiance,
            impact_estime=None,  # pas de quantité de réappro certaine à ce stade, juste une alerte de tendance
            categorie="Opportunité",
            ressource_liee_id=ressource.id,
        )

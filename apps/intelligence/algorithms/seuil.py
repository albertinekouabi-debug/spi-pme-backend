"""
Algorithme "seuil" : la stratégie la plus simple et la plus explicable —
toute Ressource déjà classée critique/à surveiller (statut calculé par
apps.resources, cf. FR-STK-02) déclenche une suggestion de réapprovisionnement.

Explicabilité (FR-SUG-01) : chaque facteur retenu (niveau actuel, seuils,
quantité suggérée) est explicite dans `facteurs`, pas une boîte noire.
"""
from decimal import Decimal

from apps.resources.models import Ressource

from .base import Algorithme, SuggestionDraft


class SeuilAlgorithme(Algorithme):
    code = "seuil"

    def generer(self, secteur) -> list[SuggestionDraft]:
        ressources = Ressource.objects.filter(
            secteur=secteur, statut__in=["critique", "a_surveiller"]
        ).only("id", "nom", "unite", "niveau_actuel", "seuil_critique", "seuil_alerte", "valeur_unitaire", "statut")

        drafts = []
        for ressource in ressources:
            quantite_suggeree = ressource.seuil_alerte - ressource.niveau_actuel
            if quantite_suggeree <= 0:
                continue

            impact = None
            if ressource.valeur_unitaire is not None:
                impact = (quantite_suggeree * ressource.valeur_unitaire).quantize(Decimal("0.01"))

            # Confiance = fonction simple de l'écart au seuil critique — un
            # niveau à 0 est nettement plus urgent qu'un niveau juste sous le
            # seuil. Heuristique documentée, pas un score "IA" opaque.
            if ressource.statut == "critique":
                ratio_epuisement = 1 - (ressource.niveau_actuel / ressource.seuil_critique) if ressource.seuil_critique else Decimal("1")
                confiance = min(Decimal("95"), Decimal("80") + ratio_epuisement * Decimal("15"))
            else:
                confiance = Decimal("65")

            drafts.append(SuggestionDraft(
                titre=f"Augmenter le stock de {ressource.nom}",
                description=(
                    f"Niveau actuel : {ressource.niveau_actuel} {ressource.unite} "
                    f"({'sous le seuil critique' if ressource.statut == 'critique' else 'sous le seuil de surveillance'})."
                ),
                facteurs={
                    "niveau_actuel": float(ressource.niveau_actuel),
                    "seuil_critique": float(ressource.seuil_critique),
                    "seuil_alerte": float(ressource.seuil_alerte),
                    "quantite_suggeree": float(quantite_suggeree),
                    "statut_ressource": ressource.statut,
                },
                confiance=round(confiance, 2),
                impact_estime=impact,
                categorie="Recommandation",
                ressource_liee_id=ressource.id,
            ))
        return drafts

"""
Moteur de détection d'alertes — même philosophie que apps.intelligence :
chaque détecteur observe le système et retourne l'ensemble des conditions
actuellement vraies pour un secteur. Le service (services.py) compare cet
ensemble à ce qui existe déjà en base et réconcilie :
  - condition nouvelle et pas d'alerte active existante -> création
  - alerte active existante et condition toujours vraie -> ne rien faire
  - alerte active existante mais condition disparue -> résolution automatique

Ajouter un détecteur = ajouter une classe + l'enregistrer dans REGISTRE, sans
toucher au service, aux vues ni aux sérialiseurs.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.db.models import Sum
from django.utils import timezone

from apps.resources.models import Ressource
from apps.tasks.models import Tache
from apps.treasury.models import Facture, Transaction

JOURS_OVERDUE_FACTURE_ELEVEE = 30
SEUIL_HAUSSE_CONSOMMATION_POURCENT = 15


@dataclass(frozen=True)
class AlerteDraft:
    type: str
    niveau: str
    titre: str
    description: str
    ressource_id: Optional[int] = None
    entite_id: Optional[int] = None
    tache_id: Optional[int] = None
    facture_id: Optional[int] = None

    @property
    def cle_objet(self):
        """Identifie l'objet concerné, quel que soit son type — utilisé pour la déduplication."""
        return self.ressource_id or self.entite_id or self.tache_id or self.facture_id


class Detecteur(ABC):
    type_code: str

    @abstractmethod
    def detecter(self, secteur) -> list[AlerteDraft]:
        raise NotImplementedError


class StockCritiqueDetecteur(Detecteur):
    type_code = "stock_critique"

    def detecter(self, secteur) -> list[AlerteDraft]:
        return [
            AlerteDraft(
                type=self.type_code, niveau="critique",
                titre=f"Stock critique : {r.nom}",
                description=f"Stock actuel : {r.niveau_actuel} {r.unite} restant(s).",
                ressource_id=r.id,
            )
            for r in Ressource.objects.filter(secteur=secteur, statut="critique")
        ]


class StockASurveillerDetecteur(Detecteur):
    type_code = "stock_a_surveiller"

    def detecter(self, secteur) -> list[AlerteDraft]:
        return [
            AlerteDraft(
                type=self.type_code, niveau="elevee",
                titre=f"{r.nom} bientôt en rupture",
                description=f"Stock actuel faible : {r.niveau_actuel} {r.unite}.",
                ressource_id=r.id,
            )
            for r in Ressource.objects.filter(secteur=secteur, statut="a_surveiller")
        ]


class FactureImpayeeDetecteur(Detecteur):
    type_code = "facture_impayee"

    def detecter(self, secteur) -> list[AlerteDraft]:
        aujourdhui = timezone.now().date()
        factures = Facture.objects.filter(
            secteur=secteur, statut__in=["emise", "impayee", "relancee"], date_echeance__lt=aujourdhui
        ).select_related("entite")

        drafts = []
        for facture in factures:
            jours_retard = (aujourdhui - facture.date_echeance).days
            niveau = "elevee" if jours_retard >= JOURS_OVERDUE_FACTURE_ELEVEE else "moderee"
            drafts.append(AlerteDraft(
                type=self.type_code, niveau=niveau,
                titre=f"Facture impayée depuis {jours_retard} jours",
                description=f"Client : {facture.entite.nom} · Montant : {facture.montant} {facture.transaction.devise if facture.transaction else 'XOF'}",
                facture_id=facture.id,
            ))
        return drafts


class TacheEnRetardDetecteur(Detecteur):
    type_code = "tache_en_retard"

    def detecter(self, secteur) -> list[AlerteDraft]:
        aujourdhui = timezone.now().date()
        taches = Tache.objects.filter(
            secteur=secteur, statut__in=["a_faire", "en_cours"], echeance__lt=aujourdhui
        )
        return [
            AlerteDraft(
                type=self.type_code, niveau="moderee",
                titre="Tâche en retard",
                description=f"{t.titre} (échéance : {t.echeance.isoformat()})",
                tache_id=t.id,
            )
            for t in taches
        ]


class ConsommationEleveeDetecteur(Detecteur):
    """
    Compare la consommation de stock (sorties, en valeur absolue) de la
    semaine en cours à celle de la semaine précédente, à l'échelle du
    secteur. Nécessite un historique sur les deux semaines pour éviter un
    faux signal en début d'activité.
    """
    type_code = "consommation_elevee"

    def detecter(self, secteur) -> list[AlerteDraft]:
        aujourdhui = timezone.now().date()
        debut_semaine_courante = aujourdhui - timedelta(days=7)
        debut_semaine_precedente = aujourdhui - timedelta(days=14)

        def consommation(depuis, jusqu_a):
            total = Transaction.objects.filter(
                secteur=secteur, type="mouvement_stock", quantite__lt=0,
                date_transaction__date__gte=depuis, date_transaction__date__lt=jusqu_a,
            ).aggregate(s=Sum("quantite"))["s"] or 0
            return abs(float(total))

        semaine_courante = consommation(debut_semaine_courante, aujourdhui)
        semaine_precedente = consommation(debut_semaine_precedente, debut_semaine_courante)

        if semaine_precedente <= 0 or semaine_courante <= 0:
            return []  # pas assez d'historique sur l'une des deux semaines : pas de faux signal

        hausse_pourcent = (semaine_courante - semaine_precedente) / semaine_precedente * 100
        if hausse_pourcent < SEUIL_HAUSSE_CONSOMMATION_POURCENT:
            return []

        return [AlerteDraft(
            type=self.type_code, niveau="moderee",
            titre="Consommation élevée cette semaine",
            description=f"Augmentation de {hausse_pourcent:.0f}% par rapport à la semaine dernière.",
        )]


REGISTRE: dict[str, type[Detecteur]] = {
    d.type_code: d for d in [
        StockCritiqueDetecteur, StockASurveillerDetecteur,
        FactureImpayeeDetecteur, TacheEnRetardDetecteur, ConsommationEleveeDetecteur,
    ]
}

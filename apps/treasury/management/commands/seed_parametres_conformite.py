"""
python manage.py seed_parametres_conformite

Initialise le seuil de déclaration CEMAC/COBAC pour les paiements en
espèces. Idempotent (get_or_create) — ne modifie pas une valeur déjà
personnalisée par l'administrateur de l'instance.

Source (vérifiée par recherche web au moment de la rédaction, juillet 2026) :
Règlement CEMAC du 11 avril 2016 relatif à la lutte contre le blanchiment
des capitaux, le financement du terrorisme et la prolifération, révisé par
le Règlement n°02/24/CEMAC/UMAC/CM du 20 décembre 2024 — interdiction des
paiements en espèces et obligation de déclaration au-delà de 5 000 000 FCFA.

IMPORTANT : les seuils réglementaires sont révisés périodiquement. Cette
valeur doit être vérifiée auprès des textes CEMAC/COBAC en vigueur avant
mise en production, et mise à jour ici (ou directement dans
ParametreSysteme) si la réglementation a changé depuis.
"""
from django.core.management.base import BaseCommand

from apps.core.models import ParametreSysteme
from apps.treasury.compliance import CLE_SEUIL_ESPECES

SOURCE = (
    "Règlement CEMAC du 11 avril 2016 (LBC/FT/P), révisé par le Règlement "
    "n°02/24/CEMAC/UMAC/CM du 20 décembre 2024. À reconfirmer auprès des "
    "textes officiels en vigueur avant toute mise en production."
)


class Command(BaseCommand):
    help = "Initialise le seuil légal CEMAC/COBAC de déclaration des paiements en espèces."

    def handle(self, *args, **options):
        parametre, cree = ParametreSysteme.objects.get_or_create(
            cle=CLE_SEUIL_ESPECES,
            secteur=None,
            defaults={
                "valeur": {"montant": 5_000_000, "devise": "XOF"},
                "description": SOURCE,
            },
        )
        if cree:
            self.stdout.write(self.style.SUCCESS(
                f"Paramètre '{CLE_SEUIL_ESPECES}' initialisé : {parametre.valeur}"
            ))
        else:
            self.stdout.write(
                f"Paramètre '{CLE_SEUIL_ESPECES}' déjà configuré : {parametre.valeur} (non modifié)."
            )

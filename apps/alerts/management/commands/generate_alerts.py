from django.core.management.base import BaseCommand

from apps.alerts.services import generer_pour_secteur
from apps.core.models import Secteur


class Command(BaseCommand):
    help = "Exécute les détecteurs d'alertes pour un secteur donné, ou tous les secteurs actifs."

    def add_arguments(self, parser):
        parser.add_argument("--secteur", type=str, help="Code du secteur. Omis = tous les secteurs actifs.")

    def handle(self, *args, **options):
        secteurs = Secteur.objects.filter(actif=True)
        if code := options.get("secteur"):
            secteurs = secteurs.filter(code=code)
            if not secteurs.exists():
                self.stderr.write(self.style.ERROR(f"Secteur '{code}' introuvable ou inactif."))
                return

        total_creees = total_resolues = 0
        for secteur in secteurs:
            resultat = generer_pour_secteur(secteur)
            total_creees += len(resultat["creees"])
            total_resolues += len(resultat["resolues_automatiquement"])
            self.stdout.write(
                f"{secteur.nom} : {len(resultat['creees'])} nouvelle(s), "
                f"{len(resultat['resolues_automatiquement'])} résolue(s) automatiquement."
            )

        self.stdout.write(self.style.SUCCESS(
            f"Terminé — {total_creees} créée(s), {total_resolues} résolue(s) au total."
        ))

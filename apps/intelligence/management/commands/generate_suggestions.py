"""
python manage.py generate_suggestions [--secteur CODE]

Pensée pour être planifiée (cron / tâche planifiée du serveur d'application),
pas appelée à chaque requête HTTP : la génération (surtout l'algorithme de
tendance, qui agrège des historiques) ne doit pas alourdir le temps de
réponse d'un écran consulté par l'utilisateur.
"""
from django.core.management.base import BaseCommand

from apps.core.models import Secteur
from apps.intelligence.services import generer_pour_secteur


class Command(BaseCommand):
    help = "Génère les suggestions IA pour un secteur donné, ou tous les secteurs actifs."

    def add_arguments(self, parser):
        parser.add_argument("--secteur", type=str, help="Code du secteur (ex. 'commerce'). Omis = tous les secteurs actifs.")

    def handle(self, *args, **options):
        secteurs = Secteur.objects.filter(actif=True)
        if code := options.get("secteur"):
            secteurs = secteurs.filter(code=code)
            if not secteurs.exists():
                self.stderr.write(self.style.ERROR(f"Secteur '{code}' introuvable ou inactif."))
                return

        total = 0
        for secteur in secteurs:
            suggestions = generer_pour_secteur(secteur)
            total += len(suggestions)
            self.stdout.write(f"{secteur.nom} : {len(suggestions)} nouvelle(s) suggestion(s).")

        self.stdout.write(self.style.SUCCESS(f"Terminé — {total} suggestion(s) créée(s) au total."))

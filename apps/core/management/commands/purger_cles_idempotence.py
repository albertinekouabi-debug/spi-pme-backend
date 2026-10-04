"""Supprime les clés d'idempotence anciennes (rétention par défaut : 7 jours, à planifier en cron)."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import CleIdempotence


class Command(BaseCommand):
    help = "Purge les clés d'idempotence plus anciennes que --jours (défaut 7)."

    def add_arguments(self, parser):
        parser.add_argument("--jours", type=int, default=7)

    def handle(self, *args, **options):
        limite = timezone.now() - timedelta(days=options["jours"])
        supprimees, _ = CleIdempotence.objects.filter(date_creation__lt=limite).delete()
        self.stdout.write(f"{supprimees} clé(s) d'idempotence purgée(s).")

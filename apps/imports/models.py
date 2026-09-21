"""
apps.imports — Import de données (CSV/XLSX).
Référence CDC : §5.8 (FR-IMP-*), §10.2.
"""
from django.conf import settings
from django.db import models

from apps.core.models import Secteur


class ImportFichier(models.Model):
    TYPES_FICHIER = [("csv", "CSV"), ("xlsx", "XLSX"), ("xls", "XLS")]
    STATUTS = [
        ("en_cours", "En cours"),
        ("termine", "Terminé"),
        ("termine_avec_anomalies", "Terminé avec anomalies"),
        ("echec", "Échec"),
    ]

    nom_fichier = models.CharField(max_length=255)
    type_fichier = models.CharField(max_length=10, choices=TYPES_FICHIER)
    taille_octets = models.BigIntegerField(null=True, blank=True)
    statut = models.CharField(max_length=30, choices=STATUTS, default="en_cours")

    mapping_colonnes = models.JSONField(default=dict, blank=True)
    lignes_totales = models.IntegerField(default=0)
    lignes_importees = models.IntegerField(default=0)
    lignes_rejetees = models.IntegerField(default=0)
    rapport_anomalies = models.JSONField(default=list, blank=True)  # [{"ligne": int, "colonne": str, "motif": str}, ...]

    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="imports"
    )
    secteur = models.ForeignKey(Secteur, on_delete=models.PROTECT, related_name="imports")

    date_import = models.DateTimeField(auto_now_add=True)
    date_fin = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "import_fichier"
        verbose_name = "Import de fichier"
        verbose_name_plural = "Imports de fichiers"
        ordering = ["-date_import"]
        indexes = [
            models.Index(fields=["secteur"]),
            models.Index(fields=["statut"]),
        ]

    def __str__(self):
        return f"{self.nom_fichier} ({self.get_statut_display()})"

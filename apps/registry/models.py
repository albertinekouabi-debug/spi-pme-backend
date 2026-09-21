"""
apps.registry — Registre central (Entité : client, fournisseur, partenaire,
patient, locataire... selon secteur).
Référence CDC : §5.2 (FR-REG-*), §10.2.
"""
from django.conf import settings
from django.db import models

from apps.core.models import Secteur


class Entite(models.Model):
    STATUTS = [
        ("actif", "Actif"),
        ("inactif", "Inactif"),
    ]

    # FR-REG-02 : le "type" est libre (piloté par la configuration sectorielle,
    # pas par un choix figé en dur) — ex. 'client', 'fournisseur', 'patient'...
    type = models.CharField(max_length=40)
    nom = models.CharField(max_length=200)
    telephone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    adresse = models.TextField(blank=True)
    ville = models.CharField(max_length=100, blank=True)
    pays = models.CharField(max_length=100, blank=True)

    # Identifiants légaux standards en zone OHADA/CEMAC (Congo-Brazzaville
    # inclus) — optionnels : une Entité peut être un particulier sans RCCM.
    numero_rccm = models.CharField(
        max_length=50, blank=True,
        help_text="Numéro RCCM (Registre du Commerce et du Crédit Mobilier), pour les entités professionnelles.",
    )
    numero_fiscal = models.CharField(
        max_length=50, blank=True,
        help_text="Numéro d'identification fiscale (NIU) de l'entité, si applicable.",
    )

    # FR-REG-02 : champs adaptés dynamiquement au secteur configuré
    champs_dynamiques = models.JSONField(default=dict, blank=True)

    statut = models.CharField(max_length=20, choices=STATUTS, default="actif")
    secteur = models.ForeignKey(Secteur, on_delete=models.PROTECT, related_name="entites")
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="entites_creees"
    )

    date_creation = models.DateTimeField(auto_now_add=True)
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "entite"
        verbose_name = "Entité"
        verbose_name_plural = "Entités"
        ordering = ["nom"]
        indexes = [
            models.Index(fields=["secteur"]),
            models.Index(fields=["type"]),
            models.Index(fields=["statut"]),
        ]

    def __str__(self):
        return f"{self.nom} ({self.type})"

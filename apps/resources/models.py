"""
apps.resources — Stock / Ressources.
Référence CDC : §5.4 (FR-STK-*), §10.2 (schéma, trigger de statut déjà défini
côté SQL dans spi_pme_schema.sql — reproduit ici en Python pour rester
portable entre PostgreSQL en production et sqlite en tests).
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import Secteur, Versionne
from apps.registry.models import Entite


class Ressource(Versionne):
    STATUTS = [
        ("critique", "Critique"),
        ("a_surveiller", "À surveiller"),
        ("stable", "Stable"),
    ]

    type = models.CharField(max_length=60)   # nature générique : "produit", "lit", "parcelle"...
    nom = models.CharField(max_length=200)
    unite = models.CharField(max_length=30, blank=True)
    # Justifié par la maquette d'import (colonne "Valeur unitaire") — sert de
    # base à un impact financier réel pour le module intelligence, plutôt
    # qu'un montant inventé. Nullable : certains secteurs (ex. santé, "lits")
    # n'ont pas de valeur unitaire monétaire pertinente.
    valeur_unitaire = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    niveau_actuel = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # Nullables : une ressource nouvellement importée (voir maquette d'import,
    # qui ne fournit ni seuil critique ni seuil d'alerte) n'a pas encore de
    # seuils configurés. Plutôt que d'inventer des valeurs par défaut
    # arbitraires, le statut reste "stable" tant qu'ils ne sont pas définis
    # (voir calculer_statut ci-dessous) — à configurer ensuite manuellement.
    seuil_critique = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    seuil_alerte = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    statut = models.CharField(max_length=20, choices=STATUTS, default="stable", editable=False)

    emplacement = models.CharField(max_length=150, blank=True)
    entite = models.ForeignKey(Entite, on_delete=models.SET_NULL, null=True, blank=True, related_name="ressources")
    secteur = models.ForeignKey(Secteur, on_delete=models.PROTECT, related_name="ressources")
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ressources_creees"
    )

    date_creation = models.DateTimeField(auto_now_add=True)
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ressource"
        verbose_name = "Ressource"
        verbose_name_plural = "Ressources"
        ordering = ["nom"]
        indexes = [
            models.Index(fields=["secteur"]),
            models.Index(fields=["statut"]),
            models.Index(fields=["type"]),
        ]

    def clean(self):
        if self.seuil_critique is not None and self.seuil_alerte is not None:
            if self.seuil_critique > self.seuil_alerte:
                raise ValidationError(
                    {"seuil_critique": "Le seuil critique doit être inférieur ou égal au seuil d'alerte."}
                )

    def calculer_statut(self) -> str:
        """FR-STK-02 : statut dérivé du niveau actuel comparé aux seuils sectoriels.
        Si les seuils ne sont pas encore configurés (ex. ressource fraîchement
        importée), on ne peut pas évaluer la criticité : "stable" par défaut,
        en attente de configuration — pas une valeur inventée par extrapolation."""
        if self.seuil_critique is None or self.seuil_alerte is None:
            return "stable"
        if self.niveau_actuel <= self.seuil_critique:
            return "critique"
        if self.niveau_actuel <= self.seuil_alerte:
            return "a_surveiller"
        return "stable"

    def save(self, *args, **kwargs):
        self.clean()
        self.statut = self.calculer_statut()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nom} ({self.get_statut_display()})"

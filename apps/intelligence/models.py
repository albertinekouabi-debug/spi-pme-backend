"""
apps.intelligence — Suggestions IA.
Référence CDC : §5.6 (FR-SUG-*), §11.3 (cycle détection → décision → exécution
→ apprentissage), §10.2.

FR-SUG-03 (aucune exécution automatique) est une garantie architecturale, pas
seulement documentaire : la seule façon de faire passer une Suggestion de
"en_attente" à "validee" avec effet réel (création de Transaction) est
`apps.intelligence.services.valider_suggestion`. Il n'existe aucun endpoint
PATCH générique sur ce modèle (voir views.py — ReadOnlyModelViewSet).
"""
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import Secteur
from apps.registry.models import Entite
from apps.resources.models import Ressource


class Suggestion(models.Model):
    TYPES_ALGORITHME = [
        ("seuil", "Seuil"),
        ("scoring_pondere", "Scoring pondéré"),
        ("moyenne_mobile", "Moyenne mobile"),
        ("regression_lineaire", "Régression linéaire"),
    ]
    STATUTS = [
        ("en_attente", "En attente"),
        ("validee", "Validée"),
        ("rejetee", "Rejetée"),
        ("ignoree", "Ignorée"),
    ]

    type_algorithme = models.CharField(max_length=30, choices=TYPES_ALGORITHME)
    categorie = models.CharField(max_length=40, blank=True)
    titre = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    facteurs = models.JSONField(default=dict, blank=True)  # FR-SUG-01 : explicabilité, jamais une boîte noire
    impact_estime = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    confiance = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    statut = models.CharField(max_length=20, choices=STATUTS, default="en_attente")
    motif_decision = models.TextField(blank=True)

    entite_liee = models.ForeignKey(Entite, on_delete=models.SET_NULL, null=True, blank=True, related_name="suggestions")
    ressource_liee = models.ForeignKey(Ressource, on_delete=models.SET_NULL, null=True, blank=True, related_name="suggestions")
    transaction_resultante = models.ForeignKey(
        "treasury.Transaction", on_delete=models.SET_NULL, null=True, blank=True, related_name="suggestion_origine"
    )
    secteur = models.ForeignKey(Secteur, on_delete=models.PROTECT, related_name="suggestions")
    decideur = models.ForeignKey(
        "accounts.Utilisateur", on_delete=models.SET_NULL, null=True, blank=True, related_name="decisions_suggestions"
    )

    date_creation = models.DateTimeField(auto_now_add=True)
    date_decision = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "suggestion"
        verbose_name = "Suggestion IA"
        verbose_name_plural = "Suggestions IA"
        ordering = ["-date_creation"]
        indexes = [
            models.Index(fields=["secteur"]),
            models.Index(fields=["statut"]),
            models.Index(fields=["type_algorithme"]),
        ]

    def clean(self):
        if self.statut == "en_attente" and self.date_decision is not None:
            raise ValidationError("Une suggestion en attente ne doit pas avoir de date de décision.")
        if self.statut != "en_attente" and self.date_decision is None:
            raise ValidationError("Une décision (validation/rejet) doit être horodatée.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.titre} ({self.get_statut_display()})"

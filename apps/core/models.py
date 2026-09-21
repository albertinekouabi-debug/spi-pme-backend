"""
apps.core — Secteur et configuration sectorielle.
Référence CDC : §2.3 (principe cœur générique + configuration sectorielle), §10.2.
"""
from django.db import models


class Secteur(models.Model):
    """Un des 11 secteurs couverts en configuration initiale (§2.4 du CDC)."""

    code = models.SlugField(max_length=30, unique=True)  # ex. 'commerce', 'sante'
    nom = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "secteur"
        verbose_name = "Secteur"
        verbose_name_plural = "Secteurs"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class ConfigurationSectorielle(models.Model):
    """
    Une configuration active par secteur : libellés, seuils, règles — stockés en
    JSONB pour éviter la duplication de schéma par secteur (§10.1 — principe directeur).
    """

    secteur = models.OneToOneField(Secteur, on_delete=models.CASCADE, related_name="configuration")
    libelles = models.JSONField(default=dict, blank=True)   # ex. {"ressource": "lit", "entite": "patient"}
    seuils = models.JSONField(default=dict, blank=True)     # ex. {"ressource.critique": 10, "ressource.alerte": 25}
    regles = models.JSONField(default=dict, blank=True)     # règles de scoring/suggestion propres au secteur
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "configuration_sectorielle"
        verbose_name = "Configuration sectorielle"
        verbose_name_plural = "Configurations sectorielles"

    def libelle_pour(self, cle_generique: str) -> str:
        """
        Traduit un concept générique ('ressource', 'entite'...) en libellé sectoriel.
        Ex. libelle_pour('ressource') -> 'Lit' en secteur Santé, 'Unité en rayon' en Commerce.
        Retombe sur la clé générique si le secteur n'a pas défini de libellé spécifique.
        """
        return self.libelles.get(cle_generique, cle_generique.capitalize())

    def __str__(self):
        return f"Configuration — {self.secteur.nom}"


class ParametreSysteme(models.Model):
    """
    Paramètres de configuration système — notamment les seuils réglementaires
    (ex. CEMAC/COBAC) qui doivent pouvoir être mis à jour sans déploiement de
    code quand la réglementation change, et jamais être des constantes
    codées en dur dans la logique applicative.

    `secteur=None` signifie un paramètre global à l'instance (ex. un seuil
    légal supranational, applicable quel que soit le secteur d'activité).
    """

    cle = models.CharField(max_length=100)
    valeur = models.JSONField()
    description = models.TextField(blank=True)
    secteur = models.ForeignKey(Secteur, on_delete=models.CASCADE, null=True, blank=True, related_name="parametres")
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "parametre_systeme"
        verbose_name = "Paramètre système"
        verbose_name_plural = "Paramètres système"
        constraints = [
            models.UniqueConstraint(fields=["cle", "secteur"], name="uq_parametre_cle_secteur"),
        ]

    def __str__(self):
        portee = self.secteur.nom if self.secteur else "global"
        return f"{self.cle} ({portee})"

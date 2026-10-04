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
    # Clôture comptable : aucune écriture (création, modification, réouverture) datée jusqu'à cette date
    # incluse. Une erreur sur une période close se corrige uniquement par une régularisation datée
    # dans la période ouverte.
    date_cloture = models.DateField(null=True, blank=True)

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


class CleIdempotence(models.Model):
    """
    Idempotence des créations (fondation de la synchronisation offline-first).

    Le client mobile met une création en file hors ligne avec une clé unique
    (UUID généré à la saisie). Si la requête part, aboutit côté serveur mais que
    la réponse se perd (timeout, coupure), le client la rejoue : le serveur
    renvoie alors la réponse ORIGINALE au lieu de créer un doublon — crucial
    pour les transactions financières et les mouvements de stock.

    `empreinte_corps` détecte l'abus/la collision : même clé avec un autre
    contenu = erreur (422), jamais un rejeu silencieux d'une autre opération.
    """

    utilisateur = models.ForeignKey("accounts.Utilisateur", on_delete=models.CASCADE, related_name="cles_idempotence")
    cle = models.CharField(max_length=64)
    methode = models.CharField(max_length=10)
    chemin = models.CharField(max_length=255)
    empreinte_corps = models.CharField(max_length=64)
    statut_http = models.PositiveSmallIntegerField()
    corps_reponse = models.JSONField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cle_idempotence"
        constraints = [models.UniqueConstraint(fields=["utilisateur", "cle"], name="uniq_idempotence_utilisateur_cle")]
        indexes = [models.Index(fields=["date_creation"])]


class Versionne(models.Model):
    """
    Contrôle de concurrence optimiste (LOT C) : `version` s'incrémente à CHAQUE sauvegarde d'un objet
    existant. Le client hors ligne mémorise la version lue ; sa modification rejouée (If-Match) est
    refusée (412) si le serveur a évolué entre-temps, au lieu d'écraser silencieusement.
    Un entier est plus fiable qu'une date : l'en-tête HTTP If-Unmodified-Since n'a qu'une précision
    d'une seconde (deux modifications dans la même seconde passeraient inaperçues).
    """

    version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            self.version = (self.version or 0) + 1
            champs = kwargs.get("update_fields")
            if champs is not None:
                supplementaires = {"version"}
                if hasattr(self, "date_maj"):
                    supplementaires.add("date_maj")  # auto_now : n'est écrit que s'il est listé
                kwargs["update_fields"] = set(champs) | supplementaires
        super().save(*args, **kwargs)

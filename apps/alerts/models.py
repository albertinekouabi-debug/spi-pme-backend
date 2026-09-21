"""
apps.alerts — Alertes.
Référence CDC : §5.7 (FR-ALR-*), §10.2.

Note d'architecture : le §10.2 du CDC ne relie l'Alerte qu'à Ressource,
Entite et Tache. La maquette (alertes.png : "Facture impayée depuis 30
jours") montre pourtant une alerte typée Trésorerie — j'ajoute donc un lien
optionnel vers Facture, cohérent avec le module treasury construit après la
rédaction initiale du schéma. Documenté ici plutôt qu'ajouté silencieusement.
"""
from django.db import models

from apps.core.models import Secteur
from apps.registry.models import Entite
from apps.resources.models import Ressource
from apps.tasks.models import Tache
from apps.treasury.models import Facture


class Alerte(models.Model):
    TYPES = [
        ("stock_critique", "Stock critique"),
        ("stock_a_surveiller", "Stock à surveiller"),
        ("facture_impayee", "Facture impayée"),
        ("tache_en_retard", "Tâche en retard"),
        ("consommation_elevee", "Consommation élevée"),
    ]
    NIVEAUX = [
        ("critique", "Critique"),
        ("elevee", "Élevée"),
        ("moderee", "Modérée"),
    ]
    STATUTS = [
        ("active", "Active"),
        ("traitee", "Traitée"),
        ("ignoree", "Ignorée"),
    ]

    type = models.CharField(max_length=40, choices=TYPES)
    niveau = models.CharField(max_length=10, choices=NIVEAUX)
    titre = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    ressource = models.ForeignKey(Ressource, on_delete=models.CASCADE, null=True, blank=True, related_name="alertes")
    entite = models.ForeignKey(Entite, on_delete=models.CASCADE, null=True, blank=True, related_name="alertes")
    tache = models.ForeignKey(Tache, on_delete=models.CASCADE, null=True, blank=True, related_name="alertes")
    facture = models.ForeignKey(Facture, on_delete=models.CASCADE, null=True, blank=True, related_name="alertes")

    statut = models.CharField(max_length=20, choices=STATUTS, default="active")
    secteur = models.ForeignKey(Secteur, on_delete=models.PROTECT, related_name="alertes")

    date_declenchement = models.DateTimeField(auto_now_add=True)
    date_resolution = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "alerte"
        verbose_name = "Alerte"
        verbose_name_plural = "Alertes"
        ordering = ["-date_declenchement"]
        indexes = [
            models.Index(fields=["secteur"]),
            models.Index(fields=["statut"]),
            models.Index(fields=["niveau"]),
            models.Index(fields=["type"]),
        ]

    def __str__(self):
        return f"{self.titre} ({self.get_niveau_display()})"

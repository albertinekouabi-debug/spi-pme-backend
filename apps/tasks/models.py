"""
apps.tasks — Tâches & Workflow.
Référence CDC : §5.5 (FR-TSK-*), §10.2.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Tache(models.Model):
    PRIORITES = [
        ("basse", "Basse"),
        ("moyenne", "Moyenne"),
        ("haute", "Haute"),
    ]
    STATUTS = [
        ("a_faire", "À faire"),
        ("en_cours", "En cours"),
        ("terminee", "Terminée"),
        ("annulee", "Annulée"),
    ]

    titre = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    categorie = models.CharField(max_length=60, blank=True)   # Approvisionnement, Reporting, Finance...
    priorite = models.CharField(max_length=10, choices=PRIORITES, default="moyenne")
    statut = models.CharField(max_length=20, choices=STATUTS, default="a_faire")
    lieu = models.CharField(max_length=150, blank=True)

    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="taches_assignees"
    )
    createur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="taches_creees"
    )
    echeance = models.DateField(null=True, blank=True)
    secteur = models.ForeignKey("core.Secteur", on_delete=models.PROTECT, related_name="taches")

    date_creation = models.DateTimeField(auto_now_add=True)
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tache"
        verbose_name = "Tâche"
        verbose_name_plural = "Tâches"
        ordering = ["echeance", "-priorite"]
        indexes = [
            models.Index(fields=["secteur"]),
            models.Index(fields=["statut"]),
            models.Index(fields=["echeance"]),
        ]

    @property
    def en_retard(self) -> bool:
        """FR-TSK-03 : une tâche non terminée dont l'échéance est dépassée est "en retard"."""
        return (
            self.statut in ("a_faire", "en_cours")
            and self.echeance is not None
            and self.echeance < timezone.now().date()
        )

    def save(self, *args, **kwargs):
        ancien_statut = None
        if self.pk:
            ancien_statut = Tache.objects.filter(pk=self.pk).values_list("statut", flat=True).first()

        super().save(*args, **kwargs)

        if ancien_statut is not None and ancien_statut != self.statut:
            TacheHistoriqueStatut.objects.create(
                tache=self, ancien_statut=ancien_statut, nouveau_statut=self.statut,
                auteur=getattr(self, "_auteur_changement", None),
            )
        elif ancien_statut is None:
            # Première création : trace aussi l'état initial (utile pour l'écran "Activité récente")
            TacheHistoriqueStatut.objects.create(
                tache=self, ancien_statut=None, nouveau_statut=self.statut,
                auteur=getattr(self, "_auteur_changement", None),
            )

    def __str__(self):
        return self.titre


class TacheHistoriqueStatut(models.Model):
    """FR-TSK-02 : historique de changement de statut."""

    tache = models.ForeignKey(Tache, on_delete=models.CASCADE, related_name="historique_statuts")
    ancien_statut = models.CharField(max_length=20, null=True, blank=True)
    nouveau_statut = models.CharField(max_length=20)
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    date_changement = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tache_historique_statut"
        verbose_name = "Historique de statut"
        verbose_name_plural = "Historiques de statut"
        ordering = ["-date_changement"]

"""
apps.audit — Journal d'audit.
Référence CDC : §5.10, §13.4 (FR-AUD-*).

FR-AUD-02 : « non modifiable a posteriori ». Double garantie :
  - ORM : save() refuse toute écriture sur une ligne déjà persistée, delete()
    refuse systématiquement (voir ci-dessous).
  - Base de données (PostgreSQL uniquement, cf. migration 0002) : triggers
    BEFORE UPDATE/DELETE levant une exception — pour qu'un accès direct à la
    base (hors ORM Django) ne puisse pas non plus altérer le journal.
"""
from django.core.exceptions import ValidationError
from django.db import models


class JournalAudit(models.Model):
    RESULTATS = [
        ("reussi", "Réussi"),
        ("avertissement", "Avertissement"),
        ("echec", "Échec"),
    ]

    action = models.CharField(max_length=80)       # ex. 'connexion', 'creation_utilisateur', 'validation_suggestion'
    module = models.CharField(max_length=40)        # accounts, treasury, intelligence, alerts, imports...
    cible_type = models.CharField(max_length=40, blank=True)  # nom du modèle concerné
    cible_id = models.CharField(max_length=40, blank=True)    # id de l'objet concerné (texte, reste générique)
    resultat = models.CharField(max_length=20, choices=RESULTATS, default="reussi")
    details = models.JSONField(default=dict, blank=True)      # contexte additionnel (avant/après, motif...)
    adresse_ip = models.GenericIPAddressField(null=True, blank=True)
    auteur = models.ForeignKey(
        "accounts.Utilisateur", on_delete=models.SET_NULL, null=True, blank=True, related_name="actions_journalisees"
    )
    date_action = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "journal_audit"
        verbose_name = "Entrée de journal d'audit"
        verbose_name_plural = "Journal d'audit"
        ordering = ["-date_action"]
        indexes = [
            models.Index(fields=["module"]),
            models.Index(fields=["auteur"]),
            models.Index(fields=["-date_action"]),
            models.Index(fields=["resultat"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Le journal d'audit est en lecture seule après création (FR-AUD-02).")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Une entrée du journal d'audit ne peut jamais être supprimée (FR-AUD-02).")

    def __str__(self):
        return f"[{self.module}] {self.action} — {self.get_resultat_display()}"

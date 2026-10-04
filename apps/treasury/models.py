"""
apps.treasury — Trésorerie (entrées/sorties financières) et mouvements de
stock (les deux partagent la table Transaction, cf. §10.2/10.3 du CDC).
Référence : §5.3 (FR-TRE-*).
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction as db_transaction

from apps.core.models import Secteur, Versionne
from apps.registry.models import Entite
from apps.resources.models import Ressource


class Transaction(Versionne):
    TYPES = [
        ("entree", "Entrée"),
        ("sortie", "Sortie"),
        ("mouvement_stock", "Mouvement de stock"),
    ]

    STATUTS = [
        ("brouillon", "Brouillon"),
        ("validee", "Validée"),
        ("contrepassee", "Contre-passée"),
    ]
    # Champs FINANCIERS : immuables dès que la transaction n'est plus un brouillon.
    # Toute correction passe par une contre-écriture (apps/treasury/workflow.py).
    CHAMPS_FINANCIERS = (
        "type", "montant", "quantite", "devise", "mode_paiement",
        "entite_id", "ressource_id", "secteur_id", "date_transaction", "reference",
    )

    type = models.CharField(max_length=20, choices=TYPES)
    reference = models.CharField(max_length=60, blank=True)   # ex. FAC-2026-0452
    description = models.CharField(max_length=255, blank=True)

    montant = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    # Pour un mouvement_stock : signé (positif = réapprovisionnement, négatif = consommation/vente).
    quantite = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    devise = models.CharField(max_length=10, default="XOF")

    MODES_PAIEMENT = [
        ("especes", "Espèces"),
        ("virement", "Virement bancaire"),
        ("mobile_money", "Mobile money"),
        ("cheque", "Chèque"),
        ("carte", "Carte bancaire"),
        ("autre", "Autre"),
    ]
    # Optionnel : nécessaire pour distinguer les transactions en espèces
    # (seules concernées par le seuil de déclaration CEMAC/COBAC, cf.
    # DeclarationConformite ci-dessous) des autres moyens de paiement.
    mode_paiement = models.CharField(max_length=20, choices=MODES_PAIEMENT, blank=True)

    entite = models.ForeignKey(Entite, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    ressource = models.ForeignKey(Ressource, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    secteur = models.ForeignKey(Secteur, on_delete=models.PROTECT, related_name="transactions")
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions"
    )

    date_transaction = models.DateTimeField()
    date_creation = models.DateTimeField(auto_now_add=True)

    # Compatibilité : une transaction créée sans préciser de statut est validée d'emblée
    # (comportement historique). Le brouillon doit être demandé explicitement.
    statut = models.CharField(max_length=15, choices=STATUTS, default="validee")
    date_maj = models.DateTimeField(auto_now=True, null=True)

    # Chaîne de correction (append-only : l'original n'est jamais modifié ni supprimé).
    #   contre_ecriture_de : cette transaction annule exactement `contre_ecriture_de`.
    #   remplace           : cette transaction est la version corrigée de `remplace`.
    contre_ecriture_de = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="contre_ecritures"
    )
    remplace = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="remplacements"
    )
    motif_correction = models.CharField(max_length=255, blank=True)
    corrigee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transactions_corrigees",
    )
    date_correction = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "transaction"
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        ordering = ["-date_transaction"]
        indexes = [
            models.Index(fields=["secteur"]),
            models.Index(fields=["type"]),
            models.Index(fields=["-date_transaction"]),
        ]

    def clean(self):
        if self.montant is None and self.quantite is None:
            raise ValidationError("Une transaction doit porter un montant, une quantité, ou les deux.")
        if self.type in ("entree", "sortie") and self.montant is None:
            raise ValidationError({"montant": "Obligatoire pour une transaction financière (entrée/sortie)."})
        if self.type == "mouvement_stock" and self.quantite is None:
            raise ValidationError({"quantite": "Obligatoire pour un mouvement de stock."})
        if self.type == "mouvement_stock" and self.ressource_id is None:
            raise ValidationError({"ressource": "Obligatoire pour un mouvement de stock."})

    def save(self, *args, **kwargs):
        self.clean()
        creation = self._state.adding
        statut_precedent = None
        if not creation:
            ancien = Transaction.objects.filter(pk=self.pk).values(*self.CHAMPS_FINANCIERS, "statut").first()
            if ancien is not None:
                statut_precedent = ancien["statut"]
                # Défense en profondeur : même hors API (service, admin, shell), une transaction
                # non brouillon ne peut pas voir ses données financières modifiées.
                if statut_precedent != "brouillon":
                    modifies = [c for c in self.CHAMPS_FINANCIERS if ancien[c] != getattr(self, c)]
                    if modifies:
                        raise ValidationError(
                            f"Transaction {statut_precedent} : champs financiers immuables ({', '.join(modifies)}). "
                            "Utiliser la contre-écriture."
                        )
        devient_validee = self.statut == "validee" and (creation or statut_precedent == "brouillon")
        # Écriture de la transaction ET effet de stock dans UNE transaction SQL : tout ou rien.
        with db_transaction.atomic():
            super().save(*args, **kwargs)
            # FR-STK-* / §10.3 : un mouvement de stock VALIDÉ répercute la quantité sur la Ressource liée
            # (statut recalculé via Ressource.save()). Un brouillon n'a aucun effet : l'effet s'applique une
            # seule fois, à la création validée ou au passage brouillon → validée.
            if devient_validee and self.type == "mouvement_stock" and self.ressource_id:
                # Verrou de ligne : sans lui, deux mouvements simultanés lisent le même niveau et le second
                # écrase le premier (mesuré : 8 mouvements de -1 → stock -2 au lieu de -8).
                ressource = Ressource.objects.select_for_update().get(pk=self.ressource_id)
                ressource.niveau_actuel = ressource.niveau_actuel + self.quantite
                ressource.save()

    def __str__(self):
        return f"{self.get_type_display()} — {self.reference or self.id}"


class Facture(Versionne):
    STATUTS = [
        ("emise", "Émise"),
        ("payee", "Payée"),
        ("impayee", "Impayée"),
        ("relancee", "Relancée"),
        ("annulee", "Annulée"),
        ("avoir", "Avoir"),
    ]
    # Champs FINANCIERS d'une facture émise : immuables (correction = avoir, jamais un PATCH).
    CHAMPS_FINANCIERS = ("numero", "montant", "taux_tva", "entite", "transaction", "secteur")

    numero = models.CharField(max_length=60, unique=True)
    transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name="factures"
    )
    entite = models.ForeignKey(Entite, on_delete=models.PROTECT, related_name="factures")
    montant = models.DecimalField(max_digits=14, decimal_places=2)
    # Optionnel et fourni par l'utilisateur — aucun taux officiel n'est
    # imposé en dur ici (les taux de TVA varient et changent par décret ;
    # les figer dans le code serait une donnée légale codée en dur, à éviter).
    # Quand renseigné, `montant` est interprété comme le montant hors taxes.
    taux_tva = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Taux de TVA en pourcentage, saisi par l'utilisateur (ex. 18.00). Laisser vide si non applicable.",
    )
    statut = models.CharField(max_length=20, choices=STATUTS, default="emise")
    date_echeance = models.DateField(null=True, blank=True)
    date_derniere_relance = models.DateTimeField(null=True, blank=True)
    secteur = models.ForeignKey(Secteur, on_delete=models.PROTECT, related_name="factures")
    date_creation = models.DateTimeField(auto_now_add=True)

    # Annulation tracée (jamais de suppression physique d'un document financier —
    # audit BE-008). Remplis uniquement quand statut == "annulee".
    date_maj = models.DateTimeField(auto_now=True, null=True)

    # Avoir (note de crédit) : facture de montant NÉGATIF reliée à la facture d'origine.
    avoir_de = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="avoirs"
    )
    motif_avoir = models.CharField(max_length=255, blank=True)

    motif_annulation = models.CharField(max_length=255, blank=True)
    annulee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="factures_annulees"
    )
    date_annulation = models.DateTimeField(null=True, blank=True)

    @property
    def montant_net(self):
        """Montant HT après avoirs (les avoirs sont des factures négatives reliées)."""
        from django.db.models import Sum
        credites = self.avoirs.aggregate(t=Sum("montant"))["t"] or 0
        return self.montant + credites

    class Meta:
        db_table = "facture"
        verbose_name = "Facture"
        verbose_name_plural = "Factures"
        ordering = ["-date_creation"]

    @property
    def montant_tva(self):
        """Calculé à partir du taux fourni par l'utilisateur — jamais un taux fixe codé en dur."""
        if self.taux_tva is None:
            return None
        return (self.montant * self.taux_tva / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def montant_ttc(self):
        if self.taux_tva is None:
            return self.montant
        return self.montant + self.montant_tva

    def __str__(self):
        return f"{self.numero} ({self.get_statut_display()})"


class DeclarationConformite(models.Model):
    """
    Obligation de déclaration réglementaire CEMAC/COBAC : les paiements en
    espèces dépassant un certain seuil doivent être déclarés à l'ANIF
    (Agence Nationale d'Investigation Financière), indépendamment de tout
    soupçon (Règlement CEMAC du 11 avril 2016, révisé par le Règlement
    n°02/24/CEMAC/UMAC/CM du 20 décembre 2024 — seuil constaté au moment de
    la rédaction : 5 000 000 FCFA, à vérifier auprès des textes officiels en
    vigueur car les seuils réglementaires sont révisés périodiquement).

    Le seuil appliqué est lu depuis ParametreSysteme au moment de la
    détection (voir apps.treasury.services.compliance) et jamais codé en dur
    ici — un changement réglementaire ne doit nécessiter qu'une mise à jour
    de configuration, pas un déploiement de code. `seuil_applique` conserve
    la valeur effective au moment de la détection, pour l'historique.
    """
    MOTIFS = [
        ("especes_superieur_seuil", "Paiement en espèces au-delà du seuil légal"),
        ("signalement_manuel", "Signalement manuel (opération jugée suspecte)"),
    ]
    STATUTS = [
        ("a_declarer", "À déclarer"),
        ("declaree", "Déclarée"),
        ("exemptee", "Exemptée (justifiée)"),
    ]

    transaction = models.OneToOneField(Transaction, on_delete=models.CASCADE, related_name="declaration_conformite")
    motif = models.CharField(max_length=30, choices=MOTIFS)
    seuil_applique = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    statut = models.CharField(max_length=20, choices=STATUTS, default="a_declarer")

    reference_declaration = models.CharField(max_length=100, blank=True)  # numéro attribué par l'ANIF après déclaration
    note = models.TextField(blank=True)  # justification, notamment si "exemptee"
    declarant = models.ForeignKey(
        "accounts.Utilisateur", on_delete=models.SET_NULL, null=True, blank=True, related_name="declarations_effectuees"
    )

    date_detection = models.DateTimeField(auto_now_add=True)
    date_declaration = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "declaration_conformite"
        verbose_name = "Déclaration de conformité"
        verbose_name_plural = "Déclarations de conformité"
        ordering = ["-date_detection"]
        indexes = [models.Index(fields=["statut"])]

    def __str__(self):
        return f"Déclaration #{self.transaction_id} ({self.get_statut_display()})"

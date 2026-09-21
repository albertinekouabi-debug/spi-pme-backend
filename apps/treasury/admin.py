from django.contrib import admin

from .models import DeclarationConformite, Facture, Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("type", "reference", "montant", "quantite", "mode_paiement", "secteur", "date_transaction")
    list_filter = ("type", "mode_paiement", "secteur")
    search_fields = ("reference", "description")


@admin.register(Facture)
class FactureAdmin(admin.ModelAdmin):
    list_display = ("numero", "entite", "montant", "taux_tva", "statut", "date_echeance")
    list_filter = ("statut", "secteur")
    search_fields = ("numero",)


@admin.register(DeclarationConformite)
class DeclarationConformiteAdmin(admin.ModelAdmin):
    list_display = ("transaction", "motif", "statut", "seuil_applique", "date_detection")
    list_filter = ("statut", "motif")
    readonly_fields = ("date_detection",)

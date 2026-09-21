from django.contrib import admin

from .models import ImportFichier


@admin.register(ImportFichier)
class ImportFichierAdmin(admin.ModelAdmin):
    list_display = ("nom_fichier", "type_fichier", "statut", "lignes_totales", "lignes_importees", "lignes_rejetees", "secteur", "date_import")
    list_filter = ("statut", "type_fichier", "secteur")
    search_fields = ("nom_fichier",)
    readonly_fields = ("rapport_anomalies",)

from django.contrib import admin

from .models import Ressource


@admin.register(Ressource)
class RessourceAdmin(admin.ModelAdmin):
    list_display = ("nom", "type", "secteur", "niveau_actuel", "statut", "date_maj")
    list_filter = ("secteur", "statut", "type")
    search_fields = ("nom",)
    readonly_fields = ("statut",)

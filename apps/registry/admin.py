from django.contrib import admin

from .models import Entite


@admin.register(Entite)
class EntiteAdmin(admin.ModelAdmin):
    list_display = ("nom", "type", "secteur", "statut", "date_creation")
    list_filter = ("secteur", "type", "statut")
    search_fields = ("nom", "email", "telephone")

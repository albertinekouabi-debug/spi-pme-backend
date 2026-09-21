from django.contrib import admin

from .models import Suggestion


@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    list_display = ("titre", "type_algorithme", "statut", "confiance", "impact_estime", "secteur", "date_creation")
    list_filter = ("statut", "type_algorithme", "secteur")
    search_fields = ("titre", "description")
    readonly_fields = ("date_creation",)

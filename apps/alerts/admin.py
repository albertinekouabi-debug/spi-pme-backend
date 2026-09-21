from django.contrib import admin

from .models import Alerte


@admin.register(Alerte)
class AlerteAdmin(admin.ModelAdmin):
    list_display = ("titre", "type", "niveau", "statut", "secteur", "date_declenchement")
    list_filter = ("statut", "niveau", "type", "secteur")
    search_fields = ("titre", "description")

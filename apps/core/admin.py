from django.contrib import admin

from .models import ConfigurationSectorielle, ParametreSysteme, Secteur


@admin.register(Secteur)
class SecteurAdmin(admin.ModelAdmin):
    list_display = ("nom", "code", "actif")


@admin.register(ConfigurationSectorielle)
class ConfigurationSectorielleAdmin(admin.ModelAdmin):
    list_display = ("secteur", "date_maj")


@admin.register(ParametreSysteme)
class ParametreSystemeAdmin(admin.ModelAdmin):
    list_display = ("cle", "secteur", "date_maj")
    list_filter = ("secteur",)

from django.contrib import admin

from .models import Tache, TacheHistoriqueStatut


class HistoriqueInline(admin.TabularInline):
    model = TacheHistoriqueStatut
    extra = 0
    readonly_fields = ("ancien_statut", "nouveau_statut", "auteur", "date_changement")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Tache)
class TacheAdmin(admin.ModelAdmin):
    list_display = ("titre", "statut", "priorite", "assignee", "echeance", "secteur")
    list_filter = ("statut", "priorite", "secteur")
    search_fields = ("titre", "description")
    inlines = [HistoriqueInline]

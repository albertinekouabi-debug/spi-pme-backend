from django.contrib import admin

from .models import JournalAudit


@admin.register(JournalAudit)
class JournalAuditAdmin(admin.ModelAdmin):
    list_display = ("action", "module", "resultat", "auteur", "cible_type", "cible_id", "date_action")
    list_filter = ("module", "resultat")
    search_fields = ("action", "cible_type", "cible_id")
    readonly_fields = [f.name for f in JournalAudit._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from django.contrib import admin

from .models import CodeInvitation, Permission, Role, RolePermission, TentativeConnexion, TokenVerificationEmail, Utilisateur, UtilisateurSecteur


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("nom", "date_creation")


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "module", "description")
    list_filter = ("module",)


@admin.register(Utilisateur)
class UtilisateurAdmin(admin.ModelAdmin):
    list_display = ("nom_utilisateur", "email", "role", "secteur_principal", "actif", "derniere_connexion")
    list_filter = ("role", "actif", "secteur_principal")
    search_fields = ("nom_utilisateur", "email", "nom_complet")


@admin.register(TentativeConnexion)
class TentativeConnexionAdmin(admin.ModelAdmin):
    list_display = ("identifiant", "utilisateur", "reussie", "adresse_ip", "date_tentative")
    list_filter = ("reussie",)
    readonly_fields = [f.name for f in TentativeConnexion._meta.fields]  # FR-IAM-04 : lecture seule

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(RolePermission)
admin.site.register(UtilisateurSecteur)


@admin.register(CodeInvitation)
class CodeInvitationAdmin(admin.ModelAdmin):
    """
    Outil intÃ©rimaire : aucun Ã©cran Administration dÃ©diÃ© n'existe encore
    cÃ´tÃ© API/Android pour gÃ©nÃ©rer des codes d'invitation (Ã  construire â€”
    cf. gap analysis). En attendant, un administrateur gÃ©nÃ¨re et communique
    le code ici mÃªme Ã  ses nouveaux employÃ©s.
    """
    list_display = ("code", "secteur", "actif", "date_expiration", "cree_par", "date_creation")
    list_filter = ("actif", "secteur")
    search_fields = ("code",)


@admin.register(TokenVerificationEmail)
class TokenVerificationEmailAdmin(admin.ModelAdmin):
    list_display = ("utilisateur", "date_expiration", "utilise_le", "date_creation")
    readonly_fields = [f.name for f in TokenVerificationEmail._meta.fields]  # ne contient qu'un hash, lecture seule

    def has_add_permission(self, request):
        return False


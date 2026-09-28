"""
apps.accounts â€” IdentitÃ© & AccÃ¨s (RBAC).
RÃ©fÃ©rence CDC : Â§5.1 (FR-IAM-*), Â§13.1 (RBAC dÃ©taillÃ©), Â§10.2 (modÃ¨le de donnÃ©es).
"""
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models

from apps.core.models import Secteur
from .managers import UtilisateurManager


class Role(models.Model):
    """Administrateur, GÃ©rant, EmployÃ©, Auditeur (Â§3.2) â€” extensible sans modification du code."""

    nom = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "role"
        verbose_name = "RÃ´le"
        verbose_name_plural = "RÃ´les"

    def __str__(self):
        return self.nom


class Permission(models.Model):
    """
    Une permission = un code opposable Ã  un endpoint (Â§13.1).
    Convention : '<module>.<action>' â€” ex. 'tresorerie.read', 'stock.write', 'audit.read'.
    """

    MODULES = [
        ("accounts", "IdentitÃ© & AccÃ¨s"),
        ("core", "Configuration sectorielle"),
        ("registry", "Registre central"),
        ("treasury", "TrÃ©sorerie"),
        ("resources", "Stock / Ressources"),
        ("tasks", "TÃ¢ches & Workflow"),
        ("intelligence", "Suggestions IA"),
        ("alerts", "Alertes"),
        ("imports", "Import de donnÃ©es"),
        ("audit", "Journal d'audit"),
    ]

    code = models.CharField(max_length=80, unique=True)
    module = models.CharField(max_length=40, choices=MODULES)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "permission"
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"
        ordering = ["module", "code"]

    def __str__(self):
        return self.code


class RolePermission(models.Model):
    """Table d'association rÃ´le â†” permissions, explicitement prÃ©vue au Â§10.2."""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="role_permissions")

    class Meta:
        db_table = "role_permission"
        unique_together = ("role", "permission")
        verbose_name = "Association rÃ´le/permission"
        verbose_name_plural = "Associations rÃ´le/permission"


class Utilisateur(AbstractBaseUser, PermissionsMixin):
    """
    Utilisateur applicatif. AUTH_USER_MODEL du projet.

    Note d'architecture (validÃ©e avec le porteur de projet) : le CDC mentionne
    Ã  la fois un secteur "principal" (Â§10.2, singulier) et un rattachement Ã 
    "un ou plusieurs secteurs" (Â§10.3, pluriel). Les deux sont conservÃ©s ici :
    `secteur_principal` = secteur affichÃ© par dÃ©faut au sÃ©lecteur de la
    maquette ("Secteur actif"), `secteurs` (via UtilisateurSecteur) = tous les
    secteurs auxquels l'utilisateur a accÃ¨s.
    """

    nom_utilisateur = models.CharField(max_length=60, unique=True)
    email = models.EmailField(unique=True)
    nom_complet = models.CharField(max_length=150, blank=True)
    telephone = models.CharField(max_length=30, blank=True)

    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="utilisateurs")
    secteur_principal = models.ForeignKey(
        Secteur, on_delete=models.SET_NULL, null=True, blank=True, related_name="utilisateurs_principaux"
    )
    secteurs = models.ManyToManyField(Secteur, through="UtilisateurSecteur", related_name="utilisateurs")

    actif = models.BooleanField(default=True)  # FR-IAM-01 : dÃ©sactivation de comptes
    mfa_active = models.BooleanField(default=False)  # MFA phase avancÃ©e (Â§13.2) â€” champ prÃªt, non exploitÃ© au MVP
    is_staff = models.BooleanField(default=False)  # accÃ¨s Ã  /admin, distinct du RBAC applicatif

    date_creation = models.DateTimeField(auto_now_add=True)
    derniere_connexion = models.DateTimeField(null=True, blank=True)

    objects = UtilisateurManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["nom_utilisateur"]

    class Meta:
        db_table = "utilisateur"
        verbose_name = "Utilisateur"
        verbose_name_plural = "Utilisateurs"

    @property
    def is_active(self):
        return self.actif

    @is_active.setter
    def is_active(self, value):
        self.actif = value

    def a_les_permissions(self, codes_requis: list[str]) -> bool:
        """VÃ©rifie que le rÃ´le de l'utilisateur porte bien tous les codes de permission requis."""
        if not codes_requis:
            return True
        codes_du_role = set(
            RolePermission.objects.filter(role_id=self.role_id).values_list("permission__code", flat=True)
        )
        return set(codes_requis).issubset(codes_du_role)

    def __str__(self):
        return f"{self.nom_utilisateur} ({self.email})"


class UtilisateurSecteur(models.Model):
    """Rattachement multi-secteur d'un utilisateur (Â§10.3)."""

    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE)
    secteur = models.ForeignKey(Secteur, on_delete=models.CASCADE)

    class Meta:
        db_table = "utilisateur_secteur"
        unique_together = ("utilisateur", "secteur")


class TentativeConnexion(models.Model):
    """FR-IAM-04 : journalisation de toute tentative de connexion, rÃ©ussie ou Ã©chouÃ©e."""

    identifiant = models.CharField(max_length=150)  # email ou nom_utilisateur saisi, mÃªme si le compte n'existe pas
    utilisateur = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name="tentatives_connexion"
    )
    reussie = models.BooleanField()
    adresse_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    date_tentative = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tentative_connexion"
        verbose_name = "Tentative de connexion"
        verbose_name_plural = "Tentatives de connexion"
        ordering = ["-date_tentative"]


class CodeInvitation(models.Model):
    """
    Auto-inscription publique (dÃ©cision produit du 16/09/2026) : un code
    d'invitation gÃ©nÃ©rÃ© par une PME (via l'admin Django pour l'instant â€”
    aucun Ã©cran dÃ©diÃ© n'existe encore cÃ´tÃ© API/Android, cf. audit) rattache
    l'inscription au bon `secteur` (= pÃ©rimÃ¨tre de donnÃ©es de l'entreprise).
    Le client n'envoie JAMAIS un secteur directement Ã  l'inscription â€” seul
    le code, rÃ©solu cÃ´tÃ© serveur, dÃ©termine le secteur. RÃ©utilisable par
    plusieurs inscriptions (pattern "lien d'invitation d'Ã©quipe") tant qu'il
    reste actif et non expirÃ©, pas Ã  usage unique.
    """

    code = models.CharField(max_length=32, unique=True)
    secteur = models.ForeignKey(Secteur, on_delete=models.CASCADE, related_name="codes_invitation")
    actif = models.BooleanField(default=True)
    date_expiration = models.DateTimeField(null=True, blank=True)
    cree_par = models.ForeignKey(
        Utilisateur, on_delete=models.SET_NULL, null=True, blank=True, related_name="codes_invitation_crees"
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "code_invitation"
        verbose_name = "Code d'invitation"
        verbose_name_plural = "Codes d'invitation"

    def est_valide(self) -> bool:
        from django.utils import timezone
        if not self.actif:
            return False
        if self.date_expiration and self.date_expiration < timezone.now():
            return False
        return True

    def __str__(self):
        return f"{self.code} â†’ {self.secteur.nom}"


class TokenVerificationEmail(models.Model):
    """
    Token de vÃ©rification d'email Ã  usage unique et durÃ©e limitÃ©e (dÃ©cision
    produit du 16/09/2026 : activation diffÃ©rÃ©e jusqu'Ã  vÃ©rification email).
    Le token brut n'est JAMAIS stockÃ© â€” seul son hash SHA-256, mÃªme logique
    de prÃ©caution qu'un mot de passe (cf. HachageMotDePasse cÃ´tÃ© Android).
    OneToOne : une nouvelle demande (renvoi d'email) remplace la prÃ©cÃ©dente
    plutÃ´t que d'accumuler des tokens orphelins.
    """

    utilisateur = models.OneToOneField(
        Utilisateur, on_delete=models.CASCADE, related_name="token_verification_email"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    date_expiration = models.DateTimeField()
    utilise_le = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "token_verification_email"
        verbose_name = "Token de vÃ©rification d'email"
        verbose_name_plural = "Tokens de vÃ©rification d'email"

    def est_valide(self) -> bool:
        from django.utils import timezone
        return self.utilise_le is None and self.date_expiration > timezone.now()


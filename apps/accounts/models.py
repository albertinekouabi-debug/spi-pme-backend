"""
apps.accounts — Identité & Accès (RBAC).
Référence CDC : §5.1 (FR-IAM-*), §13.1 (RBAC détaillé), §10.2 (modèle de données).
"""
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models

from apps.core.models import Secteur
from .managers import UtilisateurManager


class Role(models.Model):
    """Administrateur, Gérant, Employé, Auditeur (§3.2) — extensible sans modification du code."""

    nom = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "role"
        verbose_name = "Rôle"
        verbose_name_plural = "Rôles"

    def __str__(self):
        return self.nom


class Permission(models.Model):
    """
    Une permission = un code opposable à un endpoint (§13.1).
    Convention : '<module>.<action>' — ex. 'tresorerie.read', 'stock.write', 'audit.read'.
    """

    MODULES = [
        ("accounts", "Identité & Accès"),
        ("core", "Configuration sectorielle"),
        ("registry", "Registre central"),
        ("treasury", "Trésorerie"),
        ("resources", "Stock / Ressources"),
        ("tasks", "Tâches & Workflow"),
        ("intelligence", "Suggestions IA"),
        ("alerts", "Alertes"),
        ("imports", "Import de données"),
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
    """Table d'association rôle ↔ permissions, explicitement prévue au §10.2."""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="role_permissions")

    class Meta:
        db_table = "role_permission"
        unique_together = ("role", "permission")
        verbose_name = "Association rôle/permission"
        verbose_name_plural = "Associations rôle/permission"


class Utilisateur(AbstractBaseUser, PermissionsMixin):
    """
    Utilisateur applicatif. AUTH_USER_MODEL du projet.

    Note d'architecture (validée avec le porteur de projet) : le CDC mentionne
    à la fois un secteur "principal" (§10.2, singulier) et un rattachement à
    "un ou plusieurs secteurs" (§10.3, pluriel). Les deux sont conservés ici :
    `secteur_principal` = secteur affiché par défaut au sélecteur de la
    maquette ("Secteur actif"), `secteurs` (via UtilisateurSecteur) = tous les
    secteurs auxquels l'utilisateur a accès.
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

    actif = models.BooleanField(default=True)  # FR-IAM-01 : désactivation de comptes
    mfa_active = models.BooleanField(default=False)  # MFA phase avancée (§13.2) — champ prêt, non exploité au MVP
    is_staff = models.BooleanField(default=False)  # accès à /admin, distinct du RBAC applicatif

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
        """Vérifie que le rôle de l'utilisateur porte bien tous les codes de permission requis."""
        if not codes_requis:
            return True
        codes_du_role = set(
            RolePermission.objects.filter(role_id=self.role_id).values_list("permission__code", flat=True)
        )
        return set(codes_requis).issubset(codes_du_role)

    def __str__(self):
        return f"{self.nom_utilisateur} ({self.email})"


class UtilisateurSecteur(models.Model):
    """Rattachement multi-secteur d'un utilisateur (§10.3)."""

    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE)
    secteur = models.ForeignKey(Secteur, on_delete=models.CASCADE)

    class Meta:
        db_table = "utilisateur_secteur"
        unique_together = ("utilisateur", "secteur")


class TentativeConnexion(models.Model):
    """FR-IAM-04 : journalisation de toute tentative de connexion, réussie ou échouée."""

    identifiant = models.CharField(max_length=150)  # email ou nom_utilisateur saisi, même si le compte n'existe pas
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
    Auto-inscription publique (décision produit du 16/09/2026) : un code
    d'invitation généré par une PME (via l'admin Django pour l'instant —
    aucun écran dédié n'existe encore côté API/Android, cf. audit) rattache
    l'inscription au bon `secteur` (= périmètre de données de l'entreprise).
    Le client n'envoie JAMAIS un secteur directement à l'inscription — seul
    le code, résolu côté serveur, détermine le secteur. Réutilisable par
    plusieurs inscriptions (pattern "lien d'invitation d'équipe") tant qu'il
    reste actif et non expiré, pas à usage unique.
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
        return f"{self.code} → {self.secteur.nom}"


class TokenVerificationEmail(models.Model):
    """
    Token de vérification d'email à usage unique et durée limitée (décision
    produit du 16/09/2026 : activation différée jusqu'à vérification email).
    Le token brut n'est JAMAIS stocké — seul son hash SHA-256, même logique
    de précaution qu'un mot de passe (cf. HachageMotDePasse côté Android).
    OneToOne : une nouvelle demande (renvoi d'email) remplace la précédente
    plutôt que d'accumuler des tokens orphelins.
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
        verbose_name = "Token de vérification d'email"
        verbose_name_plural = "Tokens de vérification d'email"

    def est_valide(self) -> bool:
        from django.utils import timezone
        return self.utilise_le is None and self.date_expiration > timezone.now()

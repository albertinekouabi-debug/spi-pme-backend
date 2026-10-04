from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Secteur

from .models import CodeInvitation, Permission, Role, RolePermission, TentativeConnexion, TokenVerificationEmail, Utilisateur, UtilisateurSecteur

User = get_user_model()


class LoginSerializer(TokenObtainPairSerializer):
    """
    Authentification par email OU nom d'utilisateur, conformément à la maquette
    de connexion ("Email ou nom d'utilisateur" — un seul champ). Simple JWT
    attend par défaut USERNAME_FIELD strict ; on surcharge donc la résolution.
    """

    username_field = "identifiant"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["identifiant"] = serializers.CharField()
        self.fields.pop(User.USERNAME_FIELD, None)

    def validate(self, attrs):
        identifiant = attrs.get("identifiant")
        password = attrs.get("password")

        try:
            user = User.objects.get(Q(email__iexact=identifiant) | Q(nom_utilisateur=identifiant))
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {"detail": "Identifiants incorrects."}, code="authorization"
            )

        # Résolu dès que possible : un échec ultérieur (mot de passe, compte
        # inactif) reste quand même rattaché à l'utilisateur dans TentativeConnexion.
        self.user = user

        if not user.check_password(password):
            raise serializers.ValidationError(
                {"detail": "Identifiants incorrects."}, code="authorization"
            )

        if not user.actif:
            raise serializers.ValidationError(
                {"detail": "Ce compte a été désactivé. Contactez votre administrateur."}, code="inactive"
            )

        # Réutilise le pipeline standard de Simple JWT pour générer les tokens,
        # une fois l'utilisateur résolu par email OU nom d'utilisateur.
        refresh = self.get_token(user)
        data = {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "utilisateur": MoiSerializer(user).data,  # inclut les permissions (client hors ligne)
        }
        self.user = user
        return data


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "nom", "description", "date_creation"]


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "module", "description"]


class UtilisateurSerializer(serializers.ModelSerializer):
    role_nom = serializers.CharField(source="role.nom", read_only=True)
    secteur_principal_nom = serializers.CharField(source="secteur_principal.nom", read_only=True, default=None)
    secteurs = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta:
        model = Utilisateur
        fields = [
            "id", "nom_utilisateur", "email", "nom_complet", "telephone",
            "role", "role_nom", "secteur_principal", "secteur_principal_nom", "secteurs",
            "actif", "mfa_active", "date_creation", "derniere_connexion",
        ]
        read_only_fields = ["id", "date_creation", "derniere_connexion"]


class MoiSerializer(UtilisateurSerializer):
    """
    Profil de l'utilisateur connecté (GET /me) : ajoute la liste de ses permissions pour que le client
    masque/désactive les actions interdites AVANT l'appel. Indicatif : le serveur reste l'unique autorité
    (chaque endpoint revérifie le droit). Volontairement absent de UtilisateurSerializer, réutilisé dans
    les listes d'utilisateurs (éviterait un N+1).
    """

    permissions = serializers.SerializerMethodField()

    class Meta(UtilisateurSerializer.Meta):
        fields = UtilisateurSerializer.Meta.fields + ["permissions"]

    def get_permissions(self, utilisateur):
        if utilisateur.role and utilisateur.role.nom == "Administrateur":
            # Accès complet par construction (cf. HasRolePermission), même sans seed RBAC.
            from .models import Permission
            return sorted(Permission.objects.values_list("code", flat=True))
        return sorted(
            RolePermission.objects.filter(role_id=utilisateur.role_id).values_list("permission__code", flat=True)
        )


class MoiUpdateSerializer(serializers.ModelSerializer):
    """
    PATCH /api/v1/me — self-service, volontairement restreint à
    nom_complet/telephone. Ne PAS réutiliser UtilisateurSerializer ici : il
    autorise role/secteur_principal/actif en écriture (légitime pour
    UtilisateurViewSet, réservé aux administrateurs) — les exposer au
    self-service serait une élévation de privilège.
    """

    class Meta:
        model = Utilisateur
        fields = ["nom_complet", "telephone"]


class ChangerMotDePasseSerializer(serializers.Serializer):
    ancien_mot_de_passe = serializers.CharField(write_only=True)
    nouveau_mot_de_passe = serializers.CharField(write_only=True, min_length=8)

    def validate_nouveau_mot_de_passe(self, value):
        from django.contrib.auth.password_validation import validate_password as verifier_politique_mdp
        verifier_politique_mdp(value)
        return value


class DemandeReinitialisationSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ConfirmationReinitialisationSerializer(serializers.Serializer):
    token = serializers.CharField(write_only=True)
    nouveau_mot_de_passe = serializers.CharField(write_only=True, min_length=8)

    def validate_nouveau_mot_de_passe(self, value):
        from django.contrib.auth.password_validation import validate_password as verifier_politique_mdp
        verifier_politique_mdp(value)
        return value


class UtilisateurCreationSerializer(serializers.ModelSerializer):
    """FR-IAM-01 : création de compte — endpoint réservé aux Administrateurs."""

    password = serializers.CharField(write_only=True, min_length=8)
    secteurs = serializers.PrimaryKeyRelatedField(many=True, queryset=Secteur.objects.all(), required=False)

    class Meta:
        model = Utilisateur
        fields = [
            "id", "nom_utilisateur", "email", "password", "nom_complet", "telephone",
            "role", "secteur_principal", "secteurs", "actif",
        ]

    def create(self, validated_data):
        secteurs = validated_data.pop("secteurs", [])
        password = validated_data.pop("password")
        user = Utilisateur.objects.create_user(password=password, **validated_data)
        for secteur in secteurs:
            UtilisateurSecteur.objects.get_or_create(utilisateur=user, secteur=secteur)
        return user


class TentativeConnexionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TentativeConnexion
        fields = ["id", "identifiant", "utilisateur", "reussie", "adresse_ip", "user_agent", "date_tentative"]
        read_only_fields = fields


class InscriptionSerializer(serializers.ModelSerializer):
    """
    FR-IAM (auto-inscription, décision produit du 16/09/2026) :
    POST /api/v1/auth/register — endpoint PUBLIC (AllowAny).

    Rôle "Employé" et secteur imposés côté serveur depuis le code
    d'invitation — jamais transmis librement par le client, pour éviter
    toute élévation de privilège ou fuite de périmètre vers une autre PME.
    Le compte est créé inactif : voir VerificationEmailView pour l'activation.
    """

    password = serializers.CharField(write_only=True, min_length=8)
    code_invitation = serializers.CharField(write_only=True)

    class Meta:
        model = Utilisateur
        fields = ["id", "nom_utilisateur", "email", "password", "nom_complet", "telephone", "code_invitation"]
        read_only_fields = ["id"]

    def validate_code_invitation(self, value):
        try:
            code = CodeInvitation.objects.select_related("secteur").get(code=value)
        except CodeInvitation.DoesNotExist:
            raise serializers.ValidationError("Code d'invitation invalide.")
        if not code.est_valide():
            raise serializers.ValidationError("Code d'invitation expiré ou désactivé.")
        return code  # résolu directement en objet CodeInvitation, réutilisé tel quel par create()

    def validate_password(self, value):
        # Réutilise la politique de mot de passe déjà en place (AUTH_PASSWORD_VALIDATORS)
        # plutôt que d'en définir une nouvelle en parallèle.
        from django.contrib.auth.password_validation import validate_password as verifier_politique_mdp
        verifier_politique_mdp(value)
        return value

    def create(self, validated_data):
        code_invitation = validated_data.pop("code_invitation")
        password = validated_data.pop("password")
        role_employe = Role.objects.get(nom="Employé")

        user = Utilisateur.objects.create_user(
            password=password,
            role=role_employe,
            secteur_principal=code_invitation.secteur,
            actif=False,  # activation différée : vérification email requise
            **validated_data,
        )
        UtilisateurSecteur.objects.get_or_create(utilisateur=user, secteur=code_invitation.secteur)
        return user


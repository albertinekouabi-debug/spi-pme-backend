from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Secteur

from .models import CodeInvitation, Permission, Role, TentativeConnexion, TokenVerificationEmail, Utilisateur, UtilisateurSecteur

User = get_user_model()


class LoginSerializer(TokenObtainPairSerializer):
    """
    Authentification par email OU nom d'utilisateur, conformÃ©ment Ã  la maquette
    de connexion ("Email ou nom d'utilisateur" â€” un seul champ). Simple JWT
    attend par dÃ©faut USERNAME_FIELD strict ; on surcharge donc la rÃ©solution.
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

        # RÃ©solu dÃ¨s que possible : un Ã©chec ultÃ©rieur (mot de passe, compte
        # inactif) reste quand mÃªme rattachÃ© Ã  l'utilisateur dans TentativeConnexion.
        self.user = user

        if not user.check_password(password):
            raise serializers.ValidationError(
                {"detail": "Identifiants incorrects."}, code="authorization"
            )

        if not user.actif:
            raise serializers.ValidationError(
                {"detail": "Ce compte a Ã©tÃ© dÃ©sactivÃ©. Contactez votre administrateur."}, code="inactive"
            )

        # RÃ©utilise le pipeline standard de Simple JWT pour gÃ©nÃ©rer les tokens,
        # une fois l'utilisateur rÃ©solu par email OU nom d'utilisateur.
        refresh = self.get_token(user)
        data = {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "utilisateur": UtilisateurSerializer(user).data,
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


class MoiUpdateSerializer(serializers.ModelSerializer):
    """
    PATCH /api/v1/me â€” self-service, volontairement restreint Ã 
    nom_complet/telephone. Ne PAS rÃ©utiliser UtilisateurSerializer ici : il
    autorise role/secteur_principal/actif en Ã©criture (lÃ©gitime pour
    UtilisateurViewSet, rÃ©servÃ© aux administrateurs) â€” les exposer au
    self-service serait une Ã©lÃ©vation de privilÃ¨ge.
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


class UtilisateurCreationSerializer(serializers.ModelSerializer):
    """FR-IAM-01 : crÃ©ation de compte â€” endpoint rÃ©servÃ© aux Administrateurs."""

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
    FR-IAM (auto-inscription, dÃ©cision produit du 16/09/2026) :
    POST /api/v1/auth/register â€” endpoint PUBLIC (AllowAny).

    RÃ´le "EmployÃ©" et secteur imposÃ©s cÃ´tÃ© serveur depuis le code
    d'invitation â€” jamais transmis librement par le client, pour Ã©viter
    toute Ã©lÃ©vation de privilÃ¨ge ou fuite de pÃ©rimÃ¨tre vers une autre PME.
    Le compte est crÃ©Ã© inactif : voir VerificationEmailView pour l'activation.
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
            raise serializers.ValidationError("Code d'invitation expirÃ© ou dÃ©sactivÃ©.")
        return code  # rÃ©solu directement en objet CodeInvitation, rÃ©utilisÃ© tel quel par create()

    def validate_password(self, value):
        # RÃ©utilise la politique de mot de passe dÃ©jÃ  en place (AUTH_PASSWORD_VALIDATORS)
        # plutÃ´t que d'en dÃ©finir une nouvelle en parallÃ¨le.
        from django.contrib.auth.password_validation import validate_password as verifier_politique_mdp
        verifier_politique_mdp(value)
        return value

    def create(self, validated_data):
        code_invitation = validated_data.pop("code_invitation")
        password = validated_data.pop("password")
        role_employe = Role.objects.get(nom="EmployÃ©")

        user = Utilisateur.objects.create_user(
            password=password,
            role=role_employe,
            secteur_principal=code_invitation.secteur,
            actif=False,  # activation diffÃ©rÃ©e : vÃ©rification email requise
            **validated_data,
        )
        UtilisateurSecteur.objects.get_or_create(utilisateur=user, secteur=code_invitation.secteur)
        return user


import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone
from rest_framework import generics, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from .models import (
    Permission,
    Role,
    TentativeConnexion,
    TokenReinitialisationMotDePasse,
    TokenVerificationEmail,
    Utilisateur,
)
from .permissions import EstAdministrateur, HasRolePermission
from .serializers import (
    ChangerMotDePasseSerializer,
    ConfirmationReinitialisationSerializer,
    DemandeReinitialisationSerializer,
    InscriptionSerializer,
    MoiSerializer,
    LoginSerializer,
    MoiUpdateSerializer,
    PermissionSerializer,
    RoleSerializer,
    UtilisateurCreationSerializer,
    UtilisateurSerializer,
)
from .services import envoyer_email_reinitialisation, envoyer_email_verification


def _adresse_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


class LoginView(generics.GenericAPIView):
    """
    POST /api/v1/auth/login
    Body : {"identifiant": "<email ou nom d'utilisateur>", "password": "..."}

    FR-IAM-03 : émission JWT (access + refresh).
    FR-IAM-04 : journalisation de toute tentative de connexion, réussie ou échouée.
    """

    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request, *args, **kwargs):
        identifiant = request.data.get("identifiant", "")
        serializer = self.get_serializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError:
            TentativeConnexion.objects.create(
                identifiant=identifiant,
                utilisateur=getattr(serializer, "user", None),
                reussie=False,
                adresse_ip=_adresse_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
            )
            from apps.audit.services import enregistrer
            enregistrer(
                action="connexion", module="accounts", resultat="echec",
                auteur=getattr(serializer, "user", None), adresse_ip=_adresse_ip(request),
                details={"identifiant": identifiant},
            )
            raise

        user = serializer.user
        TentativeConnexion.objects.create(
            identifiant=identifiant,
            utilisateur=user,
            reussie=True,
            adresse_ip=_adresse_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
        )
        user.derniere_connexion = timezone.now()
        user.save(update_fields=["derniere_connexion"])

        from apps.audit.services import enregistrer
        enregistrer(action="connexion", module="accounts", resultat="reussi", auteur=user, adresse_ip=_adresse_ip(request))

        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class RefreshView(TokenRefreshView):
    """POST /api/v1/auth/refresh — renouvellement transparent du token d'accès (§8.3)."""

    throttle_scope = "auth"


class LogoutView(APIView):
    """
    POST /api/v1/auth/logout
    Body : {"refresh": "<token>"}
    Révoque le token de rafraîchissement (§13.2 : révocation en cas de compte compromis).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            token = RefreshToken(request.data.get("refresh", ""))
            token.blacklist()
        except TokenError:
            return Response({"detail": "Token de rafraîchissement invalide."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_205_RESET_CONTENT)


class InscriptionView(generics.CreateAPIView):
    """
    POST /api/v1/auth/register — endpoint PUBLIC (décision produit du 16/09/2026).
    Body : {"nom_utilisateur", "email", "password", "code_invitation", ...}

    Compte créé INACTIF (voir VerificationEmailView). Rôle "Employé" et
    secteur imposés côté serveur par le code d'invitation, jamais par le
    client (voir InscriptionSerializer).
    """

    serializer_class = InscriptionSerializer
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def perform_create(self, serializer):
        user = serializer.save()

        token_brut = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token_brut.encode()).hexdigest()
        TokenVerificationEmail.objects.update_or_create(
            utilisateur=user,
            defaults={
                "token_hash": token_hash,
                "date_expiration": timezone.now() + timedelta(hours=24),
                "utilise_le": None,
            },
        )
        envoyer_email_verification(user, token_brut)

        from apps.audit.services import enregistrer
        enregistrer(
            action="inscription", module="accounts", auteur=None, cible=user,
            details={"email": user.email, "secteur": user.secteur_principal_id},
        )


class VerificationEmailView(APIView):
    """
    GET /api/v1/auth/verify-email?token=<token>
    Lien cliqué depuis l'email de vérification. Active le compte si le
    token est valide, non expiré et non déjà utilisé.
    """

    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def get(self, request):
        token_brut = request.query_params.get("token", "")
        token_hash = hashlib.sha256(token_brut.encode()).hexdigest()

        try:
            enregistrement = TokenVerificationEmail.objects.select_related("utilisateur").get(token_hash=token_hash)
        except TokenVerificationEmail.DoesNotExist:
            return Response({"detail": "Lien de vérification invalide."}, status=status.HTTP_400_BAD_REQUEST)

        if not enregistrement.est_valide():
            return Response(
                {"detail": "Lien de vérification expiré ou déjà utilisé."}, status=status.HTTP_400_BAD_REQUEST
            )

        user = enregistrement.utilisateur
        user.actif = True
        user.save(update_fields=["actif"])
        enregistrement.utilise_le = timezone.now()
        enregistrement.save(update_fields=["utilise_le"])

        from apps.audit.services import enregistrer
        enregistrer(action="activation_compte", module="accounts", auteur=None, cible=user)

        return Response(
            {"detail": "Compte activé avec succès. Vous pouvez maintenant vous connecter."},
            status=status.HTTP_200_OK,
        )



class DemanderReinitialisationMotDePasseView(APIView):
    """
    POST /api/v1/auth/password-reset/request — endpoint PUBLIC.
    Body : {"email": "..."}

    Réponse 200 systématique, que l'email existe ou non (pas d'énumération
    de comptes). Si un compte actif correspond, un email est envoyé avec un
    lien à usage unique, valable 1 heure.
    """

    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        serializer = DemandeReinitialisationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            user = Utilisateur.objects.get(email__iexact=email, actif=True)
        except Utilisateur.DoesNotExist:
            return Response({"detail": "Si ce compte existe, un email a été envoyé."})

        token_brut = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token_brut.encode()).hexdigest()
        TokenReinitialisationMotDePasse.objects.update_or_create(
            utilisateur=user,
            defaults={
                "token_hash": token_hash,
                "date_expiration": timezone.now() + timedelta(hours=1),
                "utilise_le": None,
            },
        )
        envoyer_email_reinitialisation(user, token_brut)

        from apps.audit.services import enregistrer
        enregistrer(
            action="demande_reinitialisation_mot_de_passe", module="accounts",
            auteur=None, cible=user, details={"email": user.email},
        )
        return Response({"detail": "Si ce compte existe, un email a été envoyé."})


class ConfirmerReinitialisationMotDePasseView(APIView):
    """
    POST /api/v1/auth/password-reset/confirm — endpoint PUBLIC.
    Body : {"token": "...", "nouveau_mot_de_passe": "..."}

    Invalide toutes les sessions existantes de l'utilisateur (JWT SimpleJWT
    n'a pas de révocation native : on force un changement du mot de passe,
    qui est vérifié à chaque connexion, et on journalise l'événement pour
    permettre à un administrateur de surveiller les révocations forcées).
    """

    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        serializer = ConfirmationReinitialisationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token_brut = serializer.validated_data["token"]
        token_hash = hashlib.sha256(token_brut.encode()).hexdigest()

        try:
            enregistrement = TokenReinitialisationMotDePasse.objects.select_related("utilisateur").get(
                token_hash=token_hash
            )
        except TokenReinitialisationMotDePasse.DoesNotExist:
            return Response({"detail": "Lien de réinitialisation invalide."}, status=status.HTTP_400_BAD_REQUEST)

        if not enregistrement.est_valide():
            return Response(
                {"detail": "Lien de réinitialisation expiré ou déjà utilisé."}, status=status.HTTP_400_BAD_REQUEST
            )

        user = enregistrement.utilisateur
        user.set_password(serializer.validated_data["nouveau_mot_de_passe"])
        user.save(update_fields=["password"])
        enregistrement.utilise_le = timezone.now()
        enregistrement.save(update_fields=["utilise_le"])

        from apps.audit.services import enregistrer
        enregistrer(
            action="reinitialisation_mot_de_passe", module="accounts",
            auteur=None, cible=user, details={"email": user.email},
        )
        return Response({"detail": "Mot de passe réinitialisé avec succès."})


class MoiView(APIView):
    """
    GET/PATCH /api/v1/me — profil de l'utilisateur connecté.
    PATCH restreint à nom_complet/telephone (voir MoiUpdateSerializer) :
    un utilisateur ne peut jamais modifier son propre rôle, secteur,
    statut actif ou secteurs via cet endpoint.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MoiSerializer(request.user).data)

    def patch(self, request):
        serializer = MoiUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UtilisateurSerializer(request.user).data)


class ChangerMotDePasseView(APIView):
    """POST /api/v1/me/change-password — vérifie l'ancien mot de passe avant d'appliquer le nouveau."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    def post(self, request):
        serializer = ChangerMotDePasseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data["ancien_mot_de_passe"]):
            raise ValidationError({"ancien_mot_de_passe": "Mot de passe actuel incorrect."})

        user.set_password(serializer.validated_data["nouveau_mot_de_passe"])
        user.save(update_fields=["password"])

        from apps.audit.services import enregistrer
        enregistrer(action="changement_mot_de_passe", module="accounts", auteur=user, cible=user)

        return Response({"detail": "Mot de passe modifié avec succès."}, status=status.HTTP_200_OK)


class UtilisateurViewSet(viewsets.ModelViewSet):
    """
    /api/v1/users — FR-IAM-01/02 : création, modification, désactivation de comptes.
    Réservé aux Administrateurs (gestion globale, §3.2).
    """

    queryset = Utilisateur.objects.select_related("role", "secteur_principal").order_by("nom_utilisateur")
    permission_classes = [EstAdministrateur]

    def get_serializer_class(self):
        if self.action == "create":
            return UtilisateurCreationSerializer
        return UtilisateurSerializer

    def perform_create(self, serializer):
        instance = serializer.save()
        from apps.audit.services import enregistrer
        enregistrer(
            action="creation_utilisateur", module="accounts", auteur=self.request.user, cible=instance,
            details={"nom_utilisateur": instance.nom_utilisateur, "role": instance.role.nom},
        )

    def perform_destroy(self, instance):
        # FR-IAM-01 : "désactivation" plutôt que suppression physique — préserve
        # l'intégrité référentielle avec les Transaction/Tache/JournalAudit déjà liées.
        instance.actif = False
        instance.save(update_fields=["actif"])
        from apps.audit.services import enregistrer
        enregistrer(
            action="desactivation_utilisateur", module="accounts", auteur=self.request.user, cible=instance,
        )


class RoleViewSet(viewsets.ModelViewSet):
    """/api/v1/roles — réservé aux Administrateurs."""

    queryset = Role.objects.prefetch_related("role_permissions__permission").all()
    serializer_class = RoleSerializer
    permission_classes = [EstAdministrateur]


class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """/api/v1/permissions — catalogue en lecture seule, utilisé par l'écran Administration."""

    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_classes = [EstAdministrateur]


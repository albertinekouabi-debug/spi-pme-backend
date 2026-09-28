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

from .models import Permission, Role, TentativeConnexion, TokenVerificationEmail, Utilisateur
from .permissions import EstAdministrateur, HasRolePermission
from .serializers import (
    ChangerMotDePasseSerializer,
    InscriptionSerializer,
    LoginSerializer,
    MoiUpdateSerializer,
    PermissionSerializer,
    RoleSerializer,
    UtilisateurCreationSerializer,
    UtilisateurSerializer,
)
from .services import envoyer_email_verification


def _adresse_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


class LoginView(generics.GenericAPIView):
    """
    POST /api/v1/auth/login
    Body : {"identifiant": "<email ou nom d'utilisateur>", "password": "..."}

    FR-IAM-03 : Ã©mission JWT (access + refresh).
    FR-IAM-04 : journalisation de toute tentative de connexion, rÃ©ussie ou Ã©chouÃ©e.
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
    """POST /api/v1/auth/refresh â€” renouvellement transparent du token d'accÃ¨s (Â§8.3)."""

    throttle_scope = "auth"


class LogoutView(APIView):
    """
    POST /api/v1/auth/logout
    Body : {"refresh": "<token>"}
    RÃ©voque le token de rafraÃ®chissement (Â§13.2 : rÃ©vocation en cas de compte compromis).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            token = RefreshToken(request.data.get("refresh", ""))
            token.blacklist()
        except TokenError:
            return Response({"detail": "Token de rafraÃ®chissement invalide."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_205_RESET_CONTENT)


class InscriptionView(generics.CreateAPIView):
    """
    POST /api/v1/auth/register â€” endpoint PUBLIC (dÃ©cision produit du 16/09/2026).
    Body : {"nom_utilisateur", "email", "password", "code_invitation", ...}

    Compte crÃ©Ã© INACTIF (voir VerificationEmailView). RÃ´le "EmployÃ©" et
    secteur imposÃ©s cÃ´tÃ© serveur par le code d'invitation, jamais par le
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
    Lien cliquÃ© depuis l'email de vÃ©rification. Active le compte si le
    token est valide, non expirÃ© et non dÃ©jÃ  utilisÃ©.
    """

    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def get(self, request):
        token_brut = request.query_params.get("token", "")
        token_hash = hashlib.sha256(token_brut.encode()).hexdigest()

        try:
            enregistrement = TokenVerificationEmail.objects.select_related("utilisateur").get(token_hash=token_hash)
        except TokenVerificationEmail.DoesNotExist:
            return Response({"detail": "Lien de vÃ©rification invalide."}, status=status.HTTP_400_BAD_REQUEST)

        if not enregistrement.est_valide():
            return Response(
                {"detail": "Lien de vÃ©rification expirÃ© ou dÃ©jÃ  utilisÃ©."}, status=status.HTTP_400_BAD_REQUEST
            )

        user = enregistrement.utilisateur
        user.actif = True
        user.save(update_fields=["actif"])
        enregistrement.utilise_le = timezone.now()
        enregistrement.save(update_fields=["utilise_le"])

        from apps.audit.services import enregistrer
        enregistrer(action="activation_compte", module="accounts", auteur=None, cible=user)

        return Response(
            {"detail": "Compte activÃ© avec succÃ¨s. Vous pouvez maintenant vous connecter."},
            status=status.HTTP_200_OK,
        )


class MoiView(APIView):
    """
    GET/PATCH /api/v1/me â€” profil de l'utilisateur connectÃ©.
    PATCH restreint Ã  nom_complet/telephone (voir MoiUpdateSerializer) :
    un utilisateur ne peut jamais modifier son propre rÃ´le, secteur,
    statut actif ou secteurs via cet endpoint.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UtilisateurSerializer(request.user).data)

    def patch(self, request):
        serializer = MoiUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UtilisateurSerializer(request.user).data)


class ChangerMotDePasseView(APIView):
    """POST /api/v1/me/change-password â€” vÃ©rifie l'ancien mot de passe avant d'appliquer le nouveau."""

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

        return Response({"detail": "Mot de passe modifiÃ© avec succÃ¨s."}, status=status.HTTP_200_OK)


class UtilisateurViewSet(viewsets.ModelViewSet):
    """
    /api/v1/users â€” FR-IAM-01/02 : crÃ©ation, modification, dÃ©sactivation de comptes.
    RÃ©servÃ© aux Administrateurs (gestion globale, Â§3.2).
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
        # FR-IAM-01 : "dÃ©sactivation" plutÃ´t que suppression physique â€” prÃ©serve
        # l'intÃ©gritÃ© rÃ©fÃ©rentielle avec les Transaction/Tache/JournalAudit dÃ©jÃ  liÃ©es.
        instance.actif = False
        instance.save(update_fields=["actif"])
        from apps.audit.services import enregistrer
        enregistrer(
            action="desactivation_utilisateur", module="accounts", auteur=self.request.user, cible=instance,
        )


class RoleViewSet(viewsets.ModelViewSet):
    """/api/v1/roles â€” rÃ©servÃ© aux Administrateurs."""

    queryset = Role.objects.prefetch_related("role_permissions__permission").all()
    serializer_class = RoleSerializer
    permission_classes = [EstAdministrateur]


class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """/api/v1/permissions â€” catalogue en lecture seule, utilisÃ© par l'Ã©cran Administration."""

    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_classes = [EstAdministrateur]


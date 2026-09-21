from django.contrib.auth.base_user import BaseUserManager


class UtilisateurManager(BaseUserManager):
    """
    Manager pour le modèle Utilisateur personnalisé.
    L'identifiant unique de connexion est l'email (USERNAME_FIELD), mais
    l'écran de connexion (maquette connexion.png) accepte email OU nom
    d'utilisateur : cette double résolution est gérée dans le serializer
    de login (accounts/serializers.py), pas ici.
    """

    use_in_migrations = True

    def _create_user(self, email, nom_utilisateur, password, **extra_fields):
        if not email:
            raise ValueError("L'email est obligatoire.")
        if not nom_utilisateur:
            raise ValueError("Le nom d'utilisateur est obligatoire.")
        email = self.normalize_email(email)
        user = self.model(email=email, nom_utilisateur=nom_utilisateur, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, nom_utilisateur, password=None, **extra_fields):
        extra_fields.setdefault("actif", True)
        return self._create_user(email, nom_utilisateur, password, **extra_fields)

    def create_superuser(self, email, nom_utilisateur, password=None, **extra_fields):
        # Utilisé uniquement pour l'accès à /admin (bootstrap initial), pas pour le RBAC applicatif.
        extra_fields.setdefault("actif", True)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, nom_utilisateur, password, **extra_fields)

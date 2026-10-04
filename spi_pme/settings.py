"""
SPI-PME — settings.py
Pile validée : Django 5.x + Django REST Framework + Simple JWT + PostgreSQL
Référence CDC : §7 (architecture globale), §8 (backend), §13 (sécurité)
"""
import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"


def _charger_cle_secrete():
    """
    Refuse de démarrer sans clé secrète robuste (audit BE-003).

    - Production (DEBUG=False) : DJANGO_SECRET_KEY obligatoire, >= 32 caractères,
      et différente des valeurs de remplacement historiques du dépôt.
    - Développement (DEBUG=True) : une clé éphémère aléatoire est générée ;
      les sessions/tokens ne survivent pas à un redémarrage, ce qui est voulu.
    """
    cle = os.environ.get("DJANGO_SECRET_KEY", "")
    if DEBUG and not cle:
        import secrets
        return secrets.token_urlsafe(48)
    if not cle:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY est obligatoire (aucune valeur par défaut n'est acceptée)."
        )
    if len(cle) < 32 or cle.lower().startswith(("changeme", "insecure", "secret")):
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY invalide : au moins 32 caractères et pas de valeur de remplacement."
        )
    return cle


SECRET_KEY = _charger_cle_secrete()
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",  # nécessaire pour la révocation (§13.2)
    "django_filters",
    # Applications SPI-PME, alignées sur l'organisation du §8.1 du CDC
    "apps.core",
    "apps.accounts",
    "apps.registry",
    "apps.resources",
    "apps.treasury",
    "apps.tasks",
    "apps.intelligence",
    "apps.alerts",
    "apps.imports",
    "apps.audit",
    "apps.analytics",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "spi_pme.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "spi_pme.wsgi.application"

# Base de données — PostgreSQL (CDC §7.2 : préféré à MySQL pour JSONB)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "spi_pme"),
        "USER": os.environ.get("DB_USER", "spi_pme"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "accounts.Utilisateur"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "UTC"  # dates stockées en UTC, converties en Afrique/Brazzaville côté client (Android)
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ----------------------------------------------------------------------------
# Django REST Framework
# ----------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",  # FR-* : pagination systématique (§14.1)
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": ("rest_framework.throttling.ScopedRateThrottle",),
    "DEFAULT_THROTTLE_RATES": {
        "auth": "10/min",   # limitation de débit sur endpoints sensibles (§13.3)
        "imports": "20/min",
        "analytics": "60/min",
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.spi_pme_exception_handler",  # format d'erreur homogène (§14.1)
}

# ----------------------------------------------------------------------------
# Simple JWT (§8.3, §13.2)
# ----------------------------------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),   # courte durée de vie
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),      # client mobile pouvant rester déconnecté durablement
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,                  # révocation possible (§13.2)
    "UPDATE_LAST_LOGIN": False,  # géré manuellement (derniere_connexion) dans la vue de login
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# Email — vérification de compte à l'auto-inscription (décision produit du 16/09/2026).
# ATTENTION : aucun fournisseur SMTP n'a jamais été configuré dans ce projet
# (vérifié, pas supposé). Backend "console" par défaut = l'email s'affiche
# dans les logs du serveur au lieu d'être réellement envoyé — utilisable
# pour développer/tester le flux de bout en bout, PAS pour la production.
# Basculer vers un vrai SMTP nécessite une décision produit (quel
# fournisseur ?) puis DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# + les variables EMAIL_HOST/EMAIL_PORT/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD/EMAIL_USE_TLS.
EMAIL_BACKEND = os.environ.get("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.environ.get("DJANGO_DEFAULT_FROM_EMAIL", "no-reply@spi-pme.local")
EMAIL_HOST = os.environ.get("DJANGO_EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("DJANGO_EMAIL_USE_TLS", "True") == "True"
# Lien inclus dans l'email de vérification. En production, ceci doit pointer
# vers le domaine réel exposant l'API (cf. §4.2 HTTPS/Domaine du backlog).
URL_FRONTEND_VERIFICATION = os.environ.get(
    "SPI_PME_URL_VERIFICATION", "http://localhost:8000/api/v1/auth/verify-email"
)

# Lien inclus dans l'email de réinitialisation de mot de passe.
URL_FRONTEND_REINITIALISATION = os.environ.get(
    "SPI_PME_URL_REINITIALISATION", "http://localhost:8000/api/v1/auth/password-reset/confirm"
)

# CORS/HTTPS : à durcir en production via variables d'environnement (reverse proxy, §20.2)
# Par défaut : redirection HTTPS active hors développement (désactivable explicitement).
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", str(not DEBUG)) == "True"

# ----------------------------------------------------------------------------
# Durcissement production (actif dès que DEBUG est faux) — audit BE-003/§13
# ----------------------------------------------------------------------------
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    if os.environ.get("DJANGO_BEHIND_PROXY", "False") == "True":
        # Uniquement derrière un reverse proxy de confiance qui pose ce en-tête.
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Concurrence optimiste : si True, toute modification (PATCH/PUT) d'une ressource versionnée doit porter
# If-Match ou If-Unmodified-Since (sinon 428). À activer une fois tous les clients à jour.
EXIGER_PRECONDITION_MODIFICATION = os.environ.get("EXIGER_PRECONDITION_MODIFICATION", "False") == "True"

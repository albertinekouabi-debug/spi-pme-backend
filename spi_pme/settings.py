"""
SPI-PME â€” settings.py
Pile validÃ©e : Django 5.x + Django REST Framework + Simple JWT + PostgreSQL
RÃ©fÃ©rence CDC : Â§7 (architecture globale), Â§8 (backend), Â§13 (sÃ©curitÃ©)
"""
import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "changeme-in-.env-not-in-source-control")
DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"
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
    "rest_framework_simplejwt.token_blacklist",  # nÃ©cessaire pour la rÃ©vocation (Â§13.2)
    "django_filters",
    # Applications SPI-PME, alignÃ©es sur l'organisation du Â§8.1 du CDC
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

# Base de donnÃ©es â€” PostgreSQL (CDC Â§7.2 : prÃ©fÃ©rÃ© Ã  MySQL pour JSONB)
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
TIME_ZONE = "UTC"  # dates stockÃ©es en UTC, converties en Afrique/Brazzaville cÃ´tÃ© client (Android)
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
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",  # FR-* : pagination systÃ©matique (Â§14.1)
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": ("rest_framework.throttling.ScopedRateThrottle",),
    "DEFAULT_THROTTLE_RATES": {
        "auth": "10/min",   # limitation de dÃ©bit sur endpoints sensibles (Â§13.3)
        "imports": "20/min",
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.spi_pme_exception_handler",  # format d'erreur homogÃ¨ne (Â§14.1)
}

# ----------------------------------------------------------------------------
# Simple JWT (Â§8.3, Â§13.2)
# ----------------------------------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),   # courte durÃ©e de vie
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),      # client mobile pouvant rester dÃ©connectÃ© durablement
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,                  # rÃ©vocation possible (Â§13.2)
    "UPDATE_LAST_LOGIN": False,  # gÃ©rÃ© manuellement (derniere_connexion) dans la vue de login
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# Email â€” vÃ©rification de compte Ã  l'auto-inscription (dÃ©cision produit du 16/09/2026).
# ATTENTION : aucun fournisseur SMTP n'a jamais Ã©tÃ© configurÃ© dans ce projet
# (vÃ©rifiÃ©, pas supposÃ©). Backend "console" par dÃ©faut = l'email s'affiche
# dans les logs du serveur au lieu d'Ãªtre rÃ©ellement envoyÃ© â€” utilisable
# pour dÃ©velopper/tester le flux de bout en bout, PAS pour la production.
# Basculer vers un vrai SMTP nÃ©cessite une dÃ©cision produit (quel
# fournisseur ?) puis DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# + les variables EMAIL_HOST/EMAIL_PORT/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD/EMAIL_USE_TLS.
EMAIL_BACKEND = os.environ.get("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.environ.get("DJANGO_DEFAULT_FROM_EMAIL", "no-reply@spi-pme.local")
EMAIL_HOST = os.environ.get("DJANGO_EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("DJANGO_EMAIL_USE_TLS", "True") == "True"
# Lien inclus dans l'email de vÃ©rification. En production, ceci doit pointer
# vers le domaine rÃ©el exposant l'API (cf. Â§4.2 HTTPS/Domaine du backlog).
URL_FRONTEND_VERIFICATION = os.environ.get(
    "SPI_PME_URL_VERIFICATION", "http://localhost:8000/api/v1/auth/verify-email"
)

# CORS/HTTPS : Ã  durcir en production via variables d'environnement (reverse proxy, Â§20.2)
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "False") == "True"


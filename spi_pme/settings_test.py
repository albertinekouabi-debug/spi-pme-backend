"""Réglages dédiés aux tests : clé de test fixe (jamais utilisée hors tests)."""
import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-key-" + "x" * 32)

from .settings import *  # noqa: E402,F401,F403

# Le client de test Django parle en HTTP : pas de redirection HTTPS ni de cookies « secure ».
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

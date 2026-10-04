"""
Concurrence optimiste des modifications (PATCH/PUT) — LOT C.

En-têtes reconnus (RFC 7232) :
  If-Match: "<version>"           précis, recommandé pour le client mobile ;
  If-Unmodified-Since: <date>     précision d'1 s, accepté à titre de repli.
Sans en-tête : comportement historique, sauf si settings.EXIGER_PRECONDITION_MODIFICATION (→ 428).

Échec = 412 avec la version serveur et les données serveur : le client conserve sa modification locale
et propose la résolution (garder serveur / garder local / fusion / revue manuelle) — jamais d'écrasement
silencieux. Le verrou de ligne (SELECT ... FOR UPDATE) rend le contrôle atomique face à deux requêtes
simultanées.
"""
import re
from email.utils import parsedate_to_datetime

from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import APIException, ParseError
from rest_framework.response import Response

_ETAG = re.compile(r'(?:W/)?"([^"]*)"')


class PreconditionRequise(APIException):
    status_code = status.HTTP_428_PRECONDITION_REQUIRED
    default_detail = "En-tête If-Match (ou If-Unmodified-Since) requis pour modifier cette ressource."
    default_code = "precondition_requise"


class ConcurrenceOptimisteMixin:
    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            objet = self.get_object()  # contrôle d'accès + périmètre sectoriel
            verrouille = type(objet).objects.select_for_update().get(pk=objet.pk)
            conflit = self._verifier_precondition(request, verrouille)
            if conflit is not None:
                return conflit
            return super().update(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        donnees = getattr(response, "data", None)
        if response.status_code in (200, 201) and isinstance(donnees, dict) and "version" in donnees:
            response["ETag"] = f'"{donnees["version"]}"'
        return response

    def _verifier_precondition(self, request, objet):
        if_match = request.headers.get("If-Match")
        depuis = request.headers.get("If-Unmodified-Since")
        if if_match is None and depuis is None:
            if getattr(settings, "EXIGER_PRECONDITION_MODIFICATION", False):
                raise PreconditionRequise()
            return None

        if if_match is not None:  # prioritaire sur If-Unmodified-Since (RFC 7232 §3.4)
            valeur = if_match.strip()
            conforme = valeur == "*" or str(objet.version) in _ETAG.findall(valeur)
        else:
            try:
                limite = parsedate_to_datetime(depuis)
            except (TypeError, ValueError):
                raise ParseError("If-Unmodified-Since invalide (date HTTP attendue).")
            reference = getattr(objet, "date_maj", None) or getattr(objet, "date_creation", None)
            conforme = reference is None or reference.replace(microsecond=0) <= limite

        if conforme:
            return None
        # Réponse construite à la main (pas d'APIException : elle convertirait tous les nombres en
        # chaînes). Même enveloppe que le gestionnaire d'erreurs du projet (§14.1) + bloc « extra »
        # portant de quoi résoudre le conflit côté client, avec les vrais types JSON.
        return Response({
            "code": status.HTTP_412_PRECONDITION_FAILED,
            "message": "La ressource a été modifiée sur le serveur depuis votre dernière lecture.",
            "champs_invalides": None,
            "extra": {"version_serveur": objet.version, "donnees_serveur": self.get_serializer(objet).data},
        }, status=status.HTTP_412_PRECONDITION_FAILED)

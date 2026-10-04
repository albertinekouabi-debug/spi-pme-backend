"""
Mixin d'idempotence pour les créations (POST) — voir CleIdempotence.

Usage : `class XViewSet(IdempotentCreateMixin, viewsets.ModelViewSet)`.
En-tête client : `Idempotency-Key: <uuid>` (1 à 64 caractères, [A-Za-z0-9_-]).
Sans en-tête, le comportement est strictement inchangé (rétrocompatible).
"""
import functools
import hashlib
import json
import re

from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.response import Response

from .models import CleIdempotence

_CLE_VALIDE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def executer_idempotent(request, appel):
    """
    Exécute `appel()` (qui renvoie une Response DRF) sous garantie d'idempotence si la requête porte
    `Idempotency-Key`. Sans en-tête : appel direct, comportement inchangé.
    """
    cle = request.headers.get("Idempotency-Key")
    if cle is None:
        return appel()
    if not _CLE_VALIDE.match(cle):
        return Response({"detail": "Idempotency-Key invalide (1-64 caractères : lettres, chiffres, _ ou -)."},
                        status=status.HTTP_400_BAD_REQUEST)

    empreinte = hashlib.sha256(
        (request.method + request.path).encode() + b"|" + _donnees_canoniques(request)
    ).hexdigest()

    rejeu = _rejouer(request.user, cle, empreinte)
    if rejeu is not None:
        return rejeu

    try:
        with transaction.atomic():
            reponse = appel()
            if reponse.status_code < 500:
                CleIdempotence.objects.create(
                    utilisateur=request.user, cle=cle, methode=request.method, chemin=request.path,
                    empreinte_corps=empreinte, statut_http=reponse.status_code, corps_reponse=reponse.data,
                )
            return reponse
    except IntegrityError:
        # Requête concurrente avec la même clé, validée entre-temps : on rejoue sa réponse.
        rejeu = _rejouer(request.user, cle, empreinte)
        if rejeu is not None:
            return rejeu
        raise


def _rejouer(utilisateur, cle, empreinte):
    existante = CleIdempotence.objects.filter(utilisateur=utilisateur, cle=cle).first()
    if existante is None:
        return None
    if existante.empreinte_corps != empreinte:
        return Response(
            {"detail": "Cette Idempotency-Key a déjà été utilisée pour une requête différente."},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    reponse = Response(existante.corps_reponse, status=existante.statut_http)
    reponse["Idempotent-Replayed"] = "true"
    return reponse


class IdempotentCreateMixin:
    """Création (POST collection) idempotente. Pour les actions détaillées, voir `@idempotente`."""

    def create(self, request, *args, **kwargs):
        return executer_idempotent(request, lambda: super(IdempotentCreateMixin, self).create(request, *args, **kwargs))


def idempotente(vue):
    """Décorateur d'action DRF (`@action` au-dessus) : POST /x/{id}/action/ rejouable sans effet double."""

    @functools.wraps(vue)
    def enveloppe(self, request, *args, **kwargs):
        return executer_idempotent(request, lambda: vue(self, request, *args, **kwargs))

    return enveloppe


def _donnees_canoniques(request) -> bytes:
    """Empreinte stable des données PARSÉES (le flux brut n'est plus lisible après DRF)."""
    donnees = request.data
    if hasattr(donnees, "lists"):  # QueryDict (formulaire / multipart)
        donnees = {k: v for k, v in donnees.lists()}
    return json.dumps(donnees, sort_keys=True, default=str, separators=(",", ":")).encode()

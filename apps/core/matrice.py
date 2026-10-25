"""Introspection des routes de l'API : qui peut appeler quoi (source unique pour le test d'audit et la matrice)."""
import re

from django.urls import get_resolver

from apps.accounts.permissions import EstAdministrateur, HasRolePermission

METHODES = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def _parcourir(patterns, prefixe=""):
    for entree in patterns:
        if hasattr(entree, "url_patterns"):
            yield from _parcourir(entree.url_patterns, prefixe + str(entree.pattern))
        else:
            yield prefixe + str(entree.pattern), entree.callback


def _nettoyer(chemin):
    chemin = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", chemin).replace("\\", "").replace("^", "").replace("$", "")
    chemin = re.sub(r"\.\(\?P<format>.*", "", chemin)
    return "/" + chemin.strip("/")


def routes_api():
    """Une ligne par (route, méthode HTTP). `acces` : public | authentifie | administrateur | permission | AUCUN."""
    resultats = []
    for chemin, cb in _parcourir(get_resolver().url_patterns):
        if not chemin.startswith("api/v1/") or "format" in chemin:
            continue
        cls = getattr(cb, "cls", None)
        if cls is None:
            continue
        actions = getattr(cb, "actions", None)
        methodes = [m.upper() for m in actions] if actions else [m.upper() for m in METHODES if hasattr(cls, m.lower())]
        classes = list(getattr(cls, "permission_classes", []))
        noms = {c.__name__ for c in classes}
        for methode in sorted(methodes):
            codes = None
            if HasRolePermission in classes:
                req = getattr(cls, "required_permissions", None)
                if isinstance(req, dict):
                    codes = list(req.get(methode, req.get("DEFAULT", [])))
                elif req is not None:
                    codes = list(req)
            if "AllowAny" in noms or not classes:   # aucune classe déclarée (ex. SimpleJWT) = ouvert à tous
                acces = "public"
            elif EstAdministrateur in classes:
                acces = "administrateur"
            elif HasRolePermission in classes:
                acces = "permission" if codes is not None else "AUCUN (required_permissions absent)"
            elif "IsAuthenticated" in noms:
                acces = "authentifie"
            else:
                acces = "AUCUN"
            resultats.append({"chemin": _nettoyer(chemin), "methode": methode, "acces": acces, "codes": codes or []})
    return resultats

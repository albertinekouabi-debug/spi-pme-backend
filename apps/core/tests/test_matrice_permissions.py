"""
Audit de la matrice d'autorisations (côté SERVEUR, source de vérité) :
  1. aucune route de l'API sans contrôle d'accès, hors liste blanche explicite des routes publiques ;
  2. TOUTE permission exigée par une vue existe après `seed_rbac` (sinon un déploiement réel ne pourrait jamais
     l'attribuer — défaut réel déjà rencontré avec « treasury.reopen ») ;
  3. les rôles par défaut respectent le moindre privilège sur les actes sensibles.
"""
import pytest
from django.core.management import call_command

from apps.accounts.management.commands.seed_rbac import PERMISSIONS_SPECIALES, ROLES_PERMISSIONS
from apps.accounts.models import Permission
from apps.core.matrice import routes_api
from apps.treasury.workflow import PERMISSION_REOUVERTURE

ROUTES_PUBLIQUES = {
    "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/register", "/api/v1/auth/verify-email",
    "/api/v1/auth/password-reset/request", "/api/v1/auth/password-reset/confirm",
}


def test_aucune_route_sans_controle_d_acces():
    sans_controle = [r for r in routes_api() if r["acces"].startswith("AUCUN")]
    assert not sans_controle, f"Routes sans contrôle d'accès : {[(r['chemin'], r['methode']) for r in sans_controle]}"


def test_les_routes_publiques_sont_exactement_la_liste_blanche():
    publiques = {r["chemin"] for r in routes_api() if r["acces"] == "public"}
    assert publiques <= ROUTES_PUBLIQUES, f"Route publique non prévue : {publiques - ROUTES_PUBLIQUES}"


@pytest.mark.django_db
def test_toute_permission_exigee_existe_apres_le_seed():
    call_command("seed_rbac")
    existantes = set(Permission.objects.values_list("code", flat=True))
    exigees = {code for r in routes_api() for code in r["codes"]} | {PERMISSION_REOUVERTURE}
    assert exigees <= existantes, f"Permissions exigées mais jamais créées par le seed : {sorted(exigees - existantes)}"


@pytest.mark.django_db
def test_chaque_permission_speciale_est_creee():
    call_command("seed_rbac")
    for code in PERMISSIONS_SPECIALES:
        assert Permission.objects.filter(code=code).exists()


def test_moindre_privilege_sur_les_actes_sensibles():
    sensibles = {"treasury.reopen", "treasury.close"}
    assert not sensibles & set(ROLES_PERMISSIONS["Employé"])       # un employé ne rouvre ni ne clôture
    assert not sensibles & set(ROLES_PERMISSIONS["Auditeur"])      # un auditeur est en lecture seule
    assert not any(c.endswith(".write") for c in ROLES_PERMISSIONS["Auditeur"])
    assert "analytics.read" not in ROLES_PERMISSIONS["Employé"]    # pas d'analyse financière pour un employé


@pytest.mark.django_db
def test_seed_idempotent():
    call_command("seed_rbac"); n = Permission.objects.count()
    call_command("seed_rbac")
    assert Permission.objects.count() == n

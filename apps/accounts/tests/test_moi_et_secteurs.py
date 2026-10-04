import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, Utilisateur
from apps.core.models import Secteur


@pytest.fixture
def secteur_a(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def secteur_b(db):
    return Secteur.objects.create(code="sante", nom="Santé")


@pytest.fixture
def role_employe(db):
    return Role.objects.create(nom="Employé")


@pytest.fixture
def role_admin(db):
    return Role.objects.create(nom="Administrateur")


@pytest.fixture
def utilisateur(role_employe, secteur_a):
    return Utilisateur.objects.create_user(
        email="moi@acme.com", nom_utilisateur="moi", password="MotDePasse#2026",
        role=role_employe, secteur_principal=secteur_a,
    )


def client_connecte(utilisateur, password="MotDePasse#2026"):
    client = APIClient()
    reponse = client.post(
        "/api/v1/auth/login", {"identifiant": utilisateur.email, "password": password}, format="json"
    )
    assert reponse.status_code == 200, reponse.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {reponse.data['access']}")
    return client


@pytest.mark.django_db
class TestMoiView:
    def test_get_me_retourne_le_profil_de_lutilisateur_connecte(self, utilisateur):
        client = client_connecte(utilisateur)
        reponse = client.get("/api/v1/me")
        assert reponse.status_code == 200
        assert reponse.data["email"] == "moi@acme.com"

    def test_get_me_sans_authentification_est_refuse(self, db):
        client = APIClient()
        reponse = client.get("/api/v1/me")
        assert reponse.status_code == 401

    def test_patch_me_modifie_nom_complet_et_telephone(self, utilisateur):
        client = client_connecte(utilisateur)
        reponse = client.patch("/api/v1/me", {"nom_complet": "Jean Dupont", "telephone": "0600000000"}, format="json")
        assert reponse.status_code == 200
        utilisateur.refresh_from_db()
        assert utilisateur.nom_complet == "Jean Dupont"
        assert utilisateur.telephone == "0600000000"

    def test_patch_me_ne_permet_pas_de_changer_son_propre_role(self, utilisateur, role_admin):
        """Garde-fou critique : élévation de privilège via /me interdite."""
        client = client_connecte(utilisateur)
        client.patch("/api/v1/me", {"role": role_admin.id}, format="json")
        utilisateur.refresh_from_db()
        assert utilisateur.role_id != role_admin.id

    def test_patch_me_ne_permet_pas_de_sactiver_soi_meme(self, utilisateur):
        client = client_connecte(utilisateur)
        client.patch("/api/v1/me", {"actif": False}, format="json")
        utilisateur.refresh_from_db()
        assert utilisateur.actif is True

    def test_patch_me_ne_permet_pas_de_changer_son_secteur_principal(self, utilisateur, secteur_b):
        client = client_connecte(utilisateur)
        client.patch("/api/v1/me", {"secteur_principal": secteur_b.id}, format="json")
        utilisateur.refresh_from_db()
        assert utilisateur.secteur_principal_id != secteur_b.id


@pytest.mark.django_db
class TestChangerMotDePasse:
    def test_changement_reussi_avec_le_bon_ancien_mot_de_passe(self, utilisateur):
        client = client_connecte(utilisateur)
        reponse = client.post(
            "/api/v1/me/change-password",
            {"ancien_mot_de_passe": "MotDePasse#2026", "nouveau_mot_de_passe": "NouveauMdp#2026"},
            format="json",
        )
        assert reponse.status_code == 200
        utilisateur.refresh_from_db()
        assert utilisateur.check_password("NouveauMdp#2026")

    def test_refuse_si_lancien_mot_de_passe_est_incorrect(self, utilisateur):
        client = client_connecte(utilisateur)
        reponse = client.post(
            "/api/v1/me/change-password",
            {"ancien_mot_de_passe": "faux-mot-de-passe", "nouveau_mot_de_passe": "NouveauMdp#2026"},
            format="json",
        )
        assert reponse.status_code == 400
        utilisateur.refresh_from_db()
        assert utilisateur.check_password("MotDePasse#2026")  # inchangé

    def test_refuse_un_nouveau_mot_de_passe_trop_faible(self, utilisateur):
        client = client_connecte(utilisateur)
        reponse = client.post(
            "/api/v1/me/change-password",
            {"ancien_mot_de_passe": "MotDePasse#2026", "nouveau_mot_de_passe": "1234"},
            format="json",
        )
        assert reponse.status_code == 400


@pytest.mark.django_db
class TestMesSecteurs:
    def test_retourne_le_secteur_principal(self, utilisateur, secteur_a):
        client = client_connecte(utilisateur)
        reponse = client.get("/api/v1/secteurs")
        assert reponse.status_code == 200
        ids = [s["id"] for s in reponse.data["results"]] if isinstance(reponse.data, dict) else [s["id"] for s in reponse.data]
        assert secteur_a.id in ids

    def test_ne_retourne_pas_un_secteur_auquel_lutilisateur_na_pas_acces(self, utilisateur, secteur_b):
        """Le serveur ne doit jamais exposer un secteur non autorisé."""
        client = client_connecte(utilisateur)
        reponse = client.get("/api/v1/secteurs")
        ids = [s["id"] for s in reponse.data["results"]] if isinstance(reponse.data, dict) else [s["id"] for s in reponse.data]
        assert secteur_b.id not in ids

    def test_retourne_egalement_les_secteurs_secondaires(self, utilisateur, secteur_b):
        from apps.accounts.models import UtilisateurSecteur
        UtilisateurSecteur.objects.create(utilisateur=utilisateur, secteur=secteur_b)
        client = client_connecte(utilisateur)
        reponse = client.get("/api/v1/secteurs")
        ids = [s["id"] for s in reponse.data["results"]] if isinstance(reponse.data, dict) else [s["id"] for s in reponse.data]
        assert secteur_b.id in ids

    def test_sans_authentification_est_refuse(self, db):
        client = APIClient()
        reponse = client.get("/api/v1/secteurs")
        assert reponse.status_code == 401



@pytest.mark.django_db
class TestPermissionsSurMoi:
    def test_me_expose_les_permissions_du_role_triees(self):
        from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
        role = Role.objects.create(nom="Comptable")
        for code in ("treasury.write", "treasury.read"):
            p, _ = Permission.objects.get_or_create(code=code, defaults={"module": "treasury"})
            RolePermission.objects.create(role=role, permission=p)
        u = Utilisateur.objects.create_user(email="c@x.com", nom_utilisateur="c", password="MotDePasse#2026", role=role)
        c = APIClient(); c.force_authenticate(user=u)
        assert c.get("/api/v1/me").data["permissions"] == ["treasury.read", "treasury.write"]

    def test_administrateur_recoit_toutes_les_permissions_connues(self):
        from apps.accounts.models import Permission, Role, Utilisateur
        Permission.objects.get_or_create(code="treasury.reopen", defaults={"module": "treasury"})
        u = Utilisateur.objects.create_user(email="a@x.com", nom_utilisateur="a", password="MotDePasse#2026",
                                            role=Role.objects.create(nom="Administrateur"))
        c = APIClient(); c.force_authenticate(user=u)
        assert "treasury.reopen" in c.get("/api/v1/me").data["permissions"]

    def test_les_listes_d_utilisateurs_n_exposent_pas_les_permissions(self):
        from apps.accounts.serializers import UtilisateurSerializer
        assert "permissions" not in UtilisateurSerializer.Meta.fields

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, TentativeConnexion, TokenReinitialisationMotDePasse, Utilisateur
from apps.core.models import Secteur


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def role_gerant(db):
    return Role.objects.create(nom="Gérant")


@pytest.fixture
def utilisateur(db, role_gerant, secteur):
    return Utilisateur.objects.create_user(
        email="marie.k@spipme.com",
        nom_utilisateur="marie.k",
        password="MotDePasse#2026",
        role=role_gerant,
        secteur_principal=secteur,
    )


@pytest.mark.django_db
class TestLogin:
    def test_login_avec_email_reussit(self, utilisateur):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/login",
            {"identifiant": "marie.k@spipme.com", "password": "MotDePasse#2026"},
            format="json",
        )
        assert response.status_code == 200
        assert "access" in response.data and "refresh" in response.data

    def test_login_avec_nom_utilisateur_reussit(self, utilisateur):
        # FR : la maquette de connexion accepte email OU nom d'utilisateur dans le même champ
        client = APIClient()
        response = client.post(
            "/api/v1/auth/login",
            {"identifiant": "marie.k", "password": "MotDePasse#2026"},
            format="json",
        )
        assert response.status_code == 200

    def test_mauvais_mot_de_passe_echoue_et_est_journalise(self, utilisateur):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/login",
            {"identifiant": "marie.k", "password": "mauvais"},
            format="json",
        )
        assert response.status_code == 400
        # FR-IAM-04 : toute tentative échouée est journalisée, rattachée à l'utilisateur
        tentative = TentativeConnexion.objects.latest("date_tentative")
        assert tentative.reussie is False
        assert tentative.utilisateur_id == utilisateur.id

    def test_compte_desactive_refuse_la_connexion(self, utilisateur):
        utilisateur.actif = False
        utilisateur.save(update_fields=["actif"])
        client = APIClient()
        response = client.post(
            "/api/v1/auth/login",
            {"identifiant": "marie.k", "password": "MotDePasse#2026"},
            format="json",
        )
        assert response.status_code == 400
        assert "désactivé" in str(response.data)

    def test_utilisateur_inexistant_journalise_sans_compte_lie(self):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/login",
            {"identifiant": "personne@spipme.com", "password": "peu-importe"},
            format="json",
        )
        assert response.status_code == 400
        tentative = TentativeConnexion.objects.latest("date_tentative")
        assert tentative.reussie is False
        assert tentative.utilisateur is None


@pytest.mark.django_db
class TestRBAC:
    def test_employe_sans_permission_administration_est_rejete(self, secteur):
        role_employe = Role.objects.create(nom="Employé")
        employe = Utilisateur.objects.create_user(
            email="employe@spipme.com",
            nom_utilisateur="employe1",
            password="MotDePasse#2026",
            role=role_employe,
            secteur_principal=secteur,
        )
        client = APIClient()
        client.force_authenticate(user=employe)
        response = client.get("/api/v1/users/")
        # Endpoint réservé à l'Administrateur (EstAdministrateur) : un Employé doit être rejeté
        assert response.status_code == 403

    def test_administrateur_peut_lister_les_utilisateurs(self, secteur, utilisateur):
        role_admin = Role.objects.create(nom="Administrateur")
        admin = Utilisateur.objects.create_user(
            email="admin@spipme.com",
            nom_utilisateur="admin1",
            password="MotDePasse#2026",
            role=role_admin,
            secteur_principal=secteur,
        )
        client = APIClient()
        client.force_authenticate(user=admin)
        response = client.get("/api/v1/users/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestReinitialisationMotDePasse:
    """Parcours 'Mot de passe oublié' (audit AN — point d'entrée absent du client)."""

    def _utilisateur(self, mot_de_passe="AncienMdp#2026"):
        role = Role.objects.create(nom="Employé")
        return Utilisateur.objects.create_user(
            email="oubli@spipme.com", nom_utilisateur="oubli", password=mot_de_passe, role=role,
        )

    def test_demande_avec_email_existant_renvoie_200_et_cree_un_token(self, mailoutbox):
        self._utilisateur()
        r = APIClient().post("/api/v1/auth/password-reset/request", {"email": "oubli@spipme.com"}, format="json")
        assert r.status_code == 200
        assert TokenReinitialisationMotDePasse.objects.count() == 1
        assert len(mailoutbox) == 1

    def test_demande_avec_email_inconnu_renvoie_200_sans_creer_de_token(self, mailoutbox):
        r = APIClient().post("/api/v1/auth/password-reset/request", {"email": "personne@spipme.com"}, format="json")
        assert r.status_code == 200  # pas d'énumération de comptes
        assert TokenReinitialisationMotDePasse.objects.count() == 0
        assert len(mailoutbox) == 0

    def test_confirmation_avec_token_valide_change_le_mot_de_passe(self, mailoutbox):
        user = self._utilisateur()
        APIClient().post("/api/v1/auth/password-reset/request", {"email": "oubli@spipme.com"}, format="json")
        token_brut = mailoutbox[0].body.split("token=")[1].split("\n")[0].strip()

        r = APIClient().post("/api/v1/auth/password-reset/confirm", {
            "token": token_brut, "nouveau_mot_de_passe": "NouveauMdp#2026",
        }, format="json")
        assert r.status_code == 200

        user.refresh_from_db()
        assert user.check_password("NouveauMdp#2026")
        assert not user.check_password("AncienMdp#2026")

    def test_confirmation_ne_peut_pas_reutiliser_le_meme_token(self):
        self._utilisateur()
        APIClient().post("/api/v1/auth/password-reset/request", {"email": "oubli@spipme.com"}, format="json")
        from django.core import mail
        token_brut = mail.outbox[0].body.split("token=")[1].split("\n")[0].strip()
        client = APIClient()
        premiere = client.post("/api/v1/auth/password-reset/confirm", {
            "token": token_brut, "nouveau_mot_de_passe": "Premier#2026",
        }, format="json")
        assert premiere.status_code == 200
        deuxieme = client.post("/api/v1/auth/password-reset/confirm", {
            "token": token_brut, "nouveau_mot_de_passe": "Deuxieme#2026",
        }, format="json")
        assert deuxieme.status_code == 400

    def test_confirmation_avec_token_invalide_renvoie_400(self):
        r = APIClient().post("/api/v1/auth/password-reset/confirm", {
            "token": "un-token-qui-n-existe-pas", "nouveau_mot_de_passe": "NouveauMdp#2026",
        }, format="json")
        assert r.status_code == 400

    def test_confirmation_rejette_un_mot_de_passe_trop_faible(self):
        self._utilisateur()
        APIClient().post("/api/v1/auth/password-reset/request", {"email": "oubli@spipme.com"}, format="json")
        from django.core import mail
        token_brut = mail.outbox[0].body.split("token=")[1].split("\n")[0].strip()
        r = APIClient().post("/api/v1/auth/password-reset/confirm", {
            "token": token_brut, "nouveau_mot_de_passe": "1234",
        }, format="json")
        assert r.status_code == 400


@pytest.mark.django_db
def test_login_renvoie_les_permissions_du_role_pour_le_client_hors_ligne():
    from apps.accounts.models import Permission, RolePermission
    role = Role.objects.create(nom="Comptable")
    p, _ = Permission.objects.get_or_create(code="treasury.write", defaults={"module": "treasury"})
    RolePermission.objects.create(role=role, permission=p)
    Utilisateur.objects.create_user(email="c@x.com", nom_utilisateur="compta", password="MotDePasse#2026", role=role)
    r = APIClient().post("/api/v1/auth/login", {"identifiant": "compta", "password": "MotDePasse#2026"}, format="json")
    assert r.status_code == 200 and r.data["utilisateur"]["permissions"] == ["treasury.write"]

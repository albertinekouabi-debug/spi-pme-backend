import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, TentativeConnexion, Utilisateur
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

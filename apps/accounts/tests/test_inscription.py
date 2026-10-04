import hashlib
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import CodeInvitation, Role, TokenVerificationEmail, Utilisateur
from apps.core.models import Secteur


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def role_employe(db):
    return Role.objects.create(nom="Employé")


@pytest.fixture
def code_invitation(db, secteur):
    return CodeInvitation.objects.create(code="ACME2026", secteur=secteur, actif=True)


@pytest.mark.django_db
class TestInscription:
    def test_inscription_reussie_cree_compte_inactif_avec_role_employe_et_secteur_du_code(
        self, role_employe, code_invitation, secteur
    ):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/register",
            {
                "nom_utilisateur": "nouvel.employe",
                "email": "nouvel.employe@acme.com",
                "password": "MotDePasse#2026",
                "code_invitation": "ACME2026",
            },
            format="json",
        )
        assert response.status_code == 201
        user = Utilisateur.objects.get(email="nouvel.employe@acme.com")
        assert user.actif is False  # activation différée, vérification email requise
        assert user.role.nom == "Employé"
        assert user.secteur_principal_id == secteur.id
        assert user.secteurs.filter(id=secteur.id).exists()

    def test_inscription_cree_un_token_de_verification(self, role_employe, code_invitation):
        client = APIClient()
        client.post(
            "/api/v1/auth/register",
            {
                "nom_utilisateur": "test.token",
                "email": "test.token@acme.com",
                "password": "MotDePasse#2026",
                "code_invitation": "ACME2026",
            },
            format="json",
        )
        user = Utilisateur.objects.get(email="test.token@acme.com")
        assert TokenVerificationEmail.objects.filter(utilisateur=user).exists()

    def test_inscription_refuse_un_code_invitation_inconnu(self, role_employe):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/register",
            {
                "nom_utilisateur": "x",
                "email": "x@acme.com",
                "password": "MotDePasse#2026",
                "code_invitation": "INEXISTANT",
            },
            format="json",
        )
        assert response.status_code == 400
        assert not Utilisateur.objects.filter(email="x@acme.com").exists()

    def test_inscription_refuse_un_code_invitation_desactive(self, role_employe, secteur):
        CodeInvitation.objects.create(code="DESACTIVE", secteur=secteur, actif=False)
        client = APIClient()
        response = client.post(
            "/api/v1/auth/register",
            {
                "nom_utilisateur": "y",
                "email": "y@acme.com",
                "password": "MotDePasse#2026",
                "code_invitation": "DESACTIVE",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_inscription_refuse_un_code_invitation_expire(self, role_employe, secteur):
        CodeInvitation.objects.create(
            code="EXPIRE", secteur=secteur, actif=True, date_expiration=timezone.now() - timedelta(days=1)
        )
        client = APIClient()
        response = client.post(
            "/api/v1/auth/register",
            {
                "nom_utilisateur": "z",
                "email": "z@acme.com",
                "password": "MotDePasse#2026",
                "code_invitation": "EXPIRE",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_inscription_refuse_un_mot_de_passe_trop_faible(self, role_employe, code_invitation):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/register",
            {
                "nom_utilisateur": "faible",
                "email": "faible@acme.com",
                "password": "1234",
                "code_invitation": "ACME2026",
            },
            format="json",
        )
        assert response.status_code == 400
        assert not Utilisateur.objects.filter(email="faible@acme.com").exists()

    def test_inscription_refuse_un_email_deja_utilise(self, role_employe, code_invitation, secteur):
        Utilisateur.objects.create_user(
            email="existe@acme.com", nom_utilisateur="existe", password="MotDePasse#2026",
            role=role_employe, secteur_principal=secteur,
        )
        client = APIClient()
        response = client.post(
            "/api/v1/auth/register",
            {
                "nom_utilisateur": "existe2",
                "email": "existe@acme.com",
                "password": "MotDePasse#2026",
                "code_invitation": "ACME2026",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_compte_inactif_ne_peut_pas_se_connecter_avant_verification(self, role_employe, code_invitation):
        client = APIClient()
        client.post(
            "/api/v1/auth/register",
            {
                "nom_utilisateur": "pas.encore.verifie",
                "email": "pas.encore.verifie@acme.com",
                "password": "MotDePasse#2026",
                "code_invitation": "ACME2026",
            },
            format="json",
        )
        login_response = client.post(
            "/api/v1/auth/login",
            {"identifiant": "pas.encore.verifie@acme.com", "password": "MotDePasse#2026"},
            format="json",
        )
        assert login_response.status_code == 400  # LoginSerializer rejette les comptes actif=False


@pytest.mark.django_db
class TestVerificationEmail:
    def test_verification_avec_token_valide_active_le_compte(self, role_employe, secteur):
        user = Utilisateur.objects.create_user(
            email="a.verifier@acme.com", nom_utilisateur="a.verifier", password="MotDePasse#2026",
            role=role_employe, secteur_principal=secteur, actif=False,
        )
        token_brut = "token-de-test-valide"
        TokenVerificationEmail.objects.create(
            utilisateur=user,
            token_hash=hashlib.sha256(token_brut.encode()).hexdigest(),
            date_expiration=timezone.now() + timedelta(hours=24),
        )
        client = APIClient()
        response = client.get(f"/api/v1/auth/verify-email?token={token_brut}")
        assert response.status_code == 200
        user.refresh_from_db()
        assert user.actif is True

    def test_verification_avec_token_deja_utilise_echoue(self, role_employe, secteur):
        user = Utilisateur.objects.create_user(
            email="deja.verifie@acme.com", nom_utilisateur="deja.verifie", password="MotDePasse#2026",
            role=role_employe, secteur_principal=secteur, actif=False,
        )
        token_brut = "token-deja-utilise"
        TokenVerificationEmail.objects.create(
            utilisateur=user,
            token_hash=hashlib.sha256(token_brut.encode()).hexdigest(),
            date_expiration=timezone.now() + timedelta(hours=24),
            utilise_le=timezone.now(),  # déjà consommé
        )
        client = APIClient()
        response = client.get(f"/api/v1/auth/verify-email?token={token_brut}")
        assert response.status_code == 400
        user.refresh_from_db()
        assert user.actif is False  # ne doit pas s'activer une seconde fois

    def test_verification_avec_token_expire_echoue(self, role_employe, secteur):
        user = Utilisateur.objects.create_user(
            email="expire@acme.com", nom_utilisateur="expire", password="MotDePasse#2026",
            role=role_employe, secteur_principal=secteur, actif=False,
        )
        token_brut = "token-expire"
        TokenVerificationEmail.objects.create(
            utilisateur=user,
            token_hash=hashlib.sha256(token_brut.encode()).hexdigest(),
            date_expiration=timezone.now() - timedelta(hours=1),  # déjà expiré
        )
        client = APIClient()
        response = client.get(f"/api/v1/auth/verify-email?token={token_brut}")
        assert response.status_code == 400
        user.refresh_from_db()
        assert user.actif is False

    def test_verification_avec_token_inconnu_echoue(self, db):
        client = APIClient()
        response = client.get("/api/v1/auth/verify-email?token=nimporte-quoi")
        assert response.status_code == 400


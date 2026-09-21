import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role, UtilisateurSecteur
from apps.accounts.models import Utilisateur
from apps.core.models import Secteur
from apps.registry.models import Entite


@pytest.fixture
def secteur_commerce(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def secteur_sante(db):
    return Secteur.objects.create(code="sante", nom="Santé")


@pytest.fixture
def role_gerant(db):
    from apps.accounts.models import Permission, RolePermission
    role = Role.objects.create(nom="Gérant")
    for code in ["registry.read", "registry.write"]:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"module": "registry"})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    return role


@pytest.fixture
def role_admin(db):
    return Role.objects.create(nom="Administrateur")


@pytest.fixture
def gerant_commerce(db, role_gerant, secteur_commerce):
    return Utilisateur.objects.create_user(
        email="gerant.commerce@spipme.com",
        nom_utilisateur="gerant.commerce",
        password="MotDePasse#2026",
        role=role_gerant,
        secteur_principal=secteur_commerce,
    )


@pytest.fixture
def admin(db, role_admin, secteur_commerce):
    return Utilisateur.objects.create_user(
        email="admin@spipme.com",
        nom_utilisateur="admin1",
        password="MotDePasse#2026",
        role=role_admin,
        secteur_principal=secteur_commerce,
    )


@pytest.fixture
def entite_commerce(db, secteur_commerce):
    return Entite.objects.create(type="client", nom="Chez Martine SARL", secteur=secteur_commerce)


@pytest.fixture
def entite_sante(db, secteur_sante):
    return Entite.objects.create(type="patient", nom="Clinique du Plateau", secteur=secteur_sante)


@pytest.mark.django_db
class TestEntiteCRUD:
    def test_creation_par_gerant_associe_lauteur(self, gerant_commerce, secteur_commerce):
        client = APIClient()
        client.force_authenticate(user=gerant_commerce)
        response = client.post(
            "/api/v1/entities/",
            {"type": "client", "nom": "EcoFournitures SA", "secteur": secteur_commerce.id},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["cree_par"] == gerant_commerce.id

    def test_champs_dynamiques_doit_etre_un_objet(self, gerant_commerce, secteur_commerce):
        client = APIClient()
        client.force_authenticate(user=gerant_commerce)
        response = client.post(
            "/api/v1/entities/",
            {
                "type": "client", "nom": "Test", "secteur": secteur_commerce.id,
                "champs_dynamiques": "pas-un-objet",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_creation_avec_identifiants_legaux_ohada(self, gerant_commerce, secteur_commerce):
        client = APIClient()
        client.force_authenticate(user=gerant_commerce)
        response = client.post(
            "/api/v1/entities/",
            {
                "type": "fournisseur", "nom": "EcoFournitures SA", "secteur": secteur_commerce.id,
                "numero_rccm": "CG-BZV-01-2024-B12-00456", "numero_fiscal": "M2024012345P",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["numero_rccm"] == "CG-BZV-01-2024-B12-00456"

    def test_identifiants_legaux_sont_optionnels(self, gerant_commerce, secteur_commerce):
        client = APIClient()
        client.force_authenticate(user=gerant_commerce)
        response = client.post(
            "/api/v1/entities/",
            {"type": "client", "nom": "Particulier", "secteur": secteur_commerce.id},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["numero_rccm"] == ""
        role_employe = Role.objects.create(nom="Employé")  # aucune permission associée
        employe = Utilisateur.objects.create_user(
            email="employe@spipme.com", nom_utilisateur="employe1",
            password="MotDePasse#2026", role=role_employe, secteur_principal=secteur_commerce,
        )
        client = APIClient()
        client.force_authenticate(user=employe)
        response = client.get("/api/v1/entities/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestPerimetreSectoriel:
    def test_gerant_ne_voit_que_son_secteur(self, gerant_commerce, entite_commerce, entite_sante):
        client = APIClient()
        client.force_authenticate(user=gerant_commerce)
        response = client.get("/api/v1/entities/")
        assert response.status_code == 200
        noms = [e["nom"] for e in response.data["results"]]
        assert "Chez Martine SARL" in noms
        assert "Clinique du Plateau" not in noms

    def test_administrateur_voit_tous_les_secteurs(self, admin, entite_commerce, entite_sante):
        client = APIClient()
        client.force_authenticate(user=admin)
        response = client.get("/api/v1/entities/")
        assert response.status_code == 200
        assert response.data["count"] == 2

    def test_gerant_voit_aussi_ses_secteurs_secondaires(self, gerant_commerce, entite_sante, secteur_sante):
        UtilisateurSecteur.objects.create(utilisateur=gerant_commerce, secteur=secteur_sante)
        client = APIClient()
        client.force_authenticate(user=gerant_commerce)
        response = client.get("/api/v1/entities/")
        noms = [e["nom"] for e in response.data["results"]]
        assert "Clinique du Plateau" in noms


@pytest.mark.django_db
class TestResume:
    def test_resume_regroupe_par_type_reellement_present(self, admin, entite_commerce, entite_sante):
        Entite.objects.create(type="fournisseur", nom="EcoFournitures SA", secteur=entite_commerce.secteur)
        client = APIClient()
        client.force_authenticate(user=admin)
        response = client.get("/api/v1/entities/summary/")
        assert response.status_code == 200
        assert response.data["total"] == 3
        types = {ligne["type"]: ligne["total"] for ligne in response.data["par_type"]}
        assert types == {"client": 1, "patient": 1, "fournisseur": 1}

    def test_resume_respecte_le_perimetre_sectoriel(self, gerant_commerce, entite_commerce, entite_sante):
        client = APIClient()
        client.force_authenticate(user=gerant_commerce)
        response = client.get("/api/v1/entities/summary/")
        assert response.data["total"] == 1  # ne voit que son secteur (Commerce)


@pytest.mark.django_db
class TestRecherche:
    def test_recherche_par_nom(self, admin, entite_commerce, entite_sante):
        client = APIClient()
        client.force_authenticate(user=admin)
        response = client.get("/api/v1/entities/", {"q": "Martine"})
        assert response.data["count"] == 1
        assert response.data["results"][0]["nom"] == "Chez Martine SARL"

    def test_recherche_par_id(self, admin, entite_commerce):
        client = APIClient()
        client.force_authenticate(user=admin)
        response = client.get("/api/v1/entities/", {"q": str(entite_commerce.id)})
        assert response.data["count"] == 1

    def test_filtre_par_type(self, admin, entite_commerce, entite_sante):
        client = APIClient()
        client.force_authenticate(user=admin)
        response = client.get("/api/v1/entities/", {"type": "patient"})
        assert response.data["count"] == 1
        assert response.data["results"][0]["nom"] == "Clinique du Plateau"

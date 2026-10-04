"""
Isolation intersectorielle À L'ÉCRITURE (audit BE-005).

Un utilisateur rattaché au secteur A ne doit ni créer/déplacer un objet dans
le secteur B, ni lier un objet du secteur B (ressource, entité) à un objet
de son propre secteur — ce qui, pour un mouvement de stock, modifierait le
stock d'un autre secteur.
"""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import Secteur
from apps.registry.models import Entite
from apps.resources.models import Ressource
from apps.tasks.models import Tache

MODULES = ["registry", "resources", "treasury", "tasks"]


@pytest.fixture
def secteur_a(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def secteur_b(db):
    return Secteur.objects.create(code="sante", nom="Santé")


@pytest.fixture
def gerant_a(db, secteur_a):
    role = Role.objects.create(nom="Gérant")
    for module in MODULES:
        for action in ("read", "write"):
            perm, _ = Permission.objects.get_or_create(
                code=f"{module}.{action}", defaults={"module": module}
            )
            RolePermission.objects.get_or_create(role=role, permission=perm)
    return Utilisateur.objects.create_user(
        email="a@spipme.com", nom_utilisateur="gerant_a",
        password="MotDePasse#2026", role=role, secteur_principal=secteur_a,
    )


@pytest.fixture
def client_a(gerant_a):
    c = APIClient()
    c.force_authenticate(user=gerant_a)
    return c


@pytest.fixture
def ressource_b(secteur_b):
    return Ressource.objects.create(
        type="produit", nom="Stock B", secteur=secteur_b,
        niveau_actuel=100, seuil_critique=5, seuil_alerte=10,
    )


@pytest.fixture
def entite_b(secteur_b):
    return Entite.objects.create(type="client", nom="Client B", secteur=secteur_b)


@pytest.fixture
def ressource_a(secteur_a):
    return Ressource.objects.create(
        type="produit", nom="Stock A", secteur=secteur_a,
        niveau_actuel=50, seuil_critique=5, seuil_alerte=10,
    )


@pytest.mark.django_db
class TestCreationHorsPerimetre:
    def test_ressource_dans_un_autre_secteur_refusee(self, client_a, secteur_b):
        r = client_a.post("/api/v1/resources/", {
            "type": "produit", "nom": "Intrus", "secteur": secteur_b.id,
            "niveau_actuel": 1, "seuil_critique": 1, "seuil_alerte": 2,
        }, format="json")
        assert r.status_code in (400, 403)
        assert not Ressource.objects.filter(nom="Intrus").exists()

    def test_entite_dans_un_autre_secteur_refusee(self, client_a, secteur_b):
        r = client_a.post("/api/v1/entities/", {
            "type": "client", "nom": "Intrus", "secteur": secteur_b.id,
        }, format="json")
        assert r.status_code in (400, 403)
        assert not Entite.objects.filter(nom="Intrus").exists()

    def test_tache_dans_un_autre_secteur_refusee(self, client_a, secteur_b):
        r = client_a.post("/api/v1/tasks/", {
            "titre": "Intrus", "secteur": secteur_b.id,
        }, format="json")
        assert r.status_code in (400, 403)
        assert not Tache.objects.filter(titre="Intrus").exists()

    def test_transaction_dans_un_autre_secteur_refusee(self, client_a, secteur_b):
        r = client_a.post("/api/v1/transactions/", {
            "type": "entree", "montant": "10.00", "secteur": secteur_b.id,
            "date_transaction": timezone.now().isoformat(),
        }, format="json")
        assert r.status_code in (400, 403)

    def test_facture_dans_un_autre_secteur_refusee(self, client_a, secteur_b, entite_b):
        r = client_a.post("/api/v1/invoices/", {
            "numero": "FAC-X-1", "entite": entite_b.id, "montant": "10.00",
            "secteur": secteur_b.id,
        }, format="json")
        assert r.status_code in (400, 403)


@pytest.mark.django_db
class TestDeplacementHorsPerimetre:
    def test_deplacer_sa_ressource_vers_un_autre_secteur_refuse(self, client_a, ressource_a, secteur_b):
        r = client_a.patch(f"/api/v1/resources/{ressource_a.id}/", {"secteur": secteur_b.id}, format="json")
        assert r.status_code in (400, 403)
        ressource_a.refresh_from_db()
        assert ressource_a.secteur_id != secteur_b.id


@pytest.mark.django_db
class TestLiensEntreSecteurs:
    def test_mouvement_de_stock_sur_ressource_dun_autre_secteur_refuse(
        self, client_a, secteur_a, ressource_b
    ):
        r = client_a.post("/api/v1/transactions/", {
            "type": "mouvement_stock", "quantite": "-90", "secteur": secteur_a.id,
            "ressource": ressource_b.id, "date_transaction": timezone.now().isoformat(),
        }, format="json")
        assert r.status_code in (400, 403)
        ressource_b.refresh_from_db()
        assert ressource_b.niveau_actuel == 100  # stock d'un autre secteur intact

    def test_ressource_liee_a_une_entite_dun_autre_secteur_refusee(
        self, client_a, secteur_a, entite_b
    ):
        r = client_a.post("/api/v1/resources/", {
            "type": "produit", "nom": "Lien", "secteur": secteur_a.id, "entite": entite_b.id,
            "niveau_actuel": 1, "seuil_critique": 1, "seuil_alerte": 2,
        }, format="json")
        assert r.status_code == 400

    def test_facture_sur_entite_dun_autre_secteur_refusee(self, client_a, secteur_a, entite_b):
        r = client_a.post("/api/v1/invoices/", {
            "numero": "FAC-X-2", "entite": entite_b.id, "montant": "10.00", "secteur": secteur_a.id,
        }, format="json")
        assert r.status_code == 400


@pytest.mark.django_db
class TestCasNominauxPreserves:
    def test_creation_dans_son_propre_secteur_reste_possible(self, client_a, secteur_a):
        r = client_a.post("/api/v1/resources/", {
            "type": "produit", "nom": "OK", "secteur": secteur_a.id,
            "niveau_actuel": 10, "seuil_critique": 1, "seuil_alerte": 2,
        }, format="json")
        assert r.status_code == 201

    def test_mouvement_de_stock_dans_son_secteur_reste_possible(self, client_a, secteur_a, ressource_a):
        r = client_a.post("/api/v1/transactions/", {
            "type": "mouvement_stock", "quantite": "-5", "secteur": secteur_a.id,
            "ressource": ressource_a.id, "date_transaction": timezone.now().isoformat(),
        }, format="json")
        assert r.status_code == 201
        ressource_a.refresh_from_db()
        assert ressource_a.niveau_actuel == 45

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import Secteur
from apps.resources.models import Ressource


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def role_gerant(db):
    role = Role.objects.create(nom="Gérant")
    for code in ["resources.read", "resources.write"]:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"module": "resources"})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    return role


@pytest.fixture
def gerant(db, role_gerant, secteur):
    return Utilisateur.objects.create_user(
        email="gerant@spipme.com", nom_utilisateur="gerant1",
        password="MotDePasse#2026", role=role_gerant, secteur_principal=secteur,
    )


@pytest.mark.django_db
class TestCalculStatut:
    def test_statut_critique_quand_sous_le_seuil_critique(self, secteur):
        r = Ressource.objects.create(
            type="produit", nom="Lait en poudre", secteur=secteur,
            niveau_actuel=2, seuil_critique=10, seuil_alerte=25,
        )
        assert r.statut == "critique"

    def test_statut_a_surveiller_entre_les_deux_seuils(self, secteur):
        r = Ressource.objects.create(
            type="produit", nom="Riz étuvé 25kg", secteur=secteur,
            niveau_actuel=18, seuil_critique=10, seuil_alerte=50,
        )
        assert r.statut == "a_surveiller"

    def test_statut_stable_au_dessus_du_seuil_alerte(self, secteur):
        r = Ressource.objects.create(
            type="produit", nom="Huile 5L", secteur=secteur,
            niveau_actuel=120, seuil_critique=10, seuil_alerte=20,
        )
        assert r.statut == "stable"

    def test_statut_se_recalcule_a_la_mise_a_jour(self, secteur):
        r = Ressource.objects.create(
            type="produit", nom="Sucre 50kg", secteur=secteur,
            niveau_actuel=85, seuil_critique=30, seuil_alerte=50,
        )
        assert r.statut == "stable"
        r.niveau_actuel = 5
        r.save()
        assert r.statut == "critique"

    def test_seuil_critique_superieur_au_seuil_alerte_est_rejete(self, secteur):
        from django.core.exceptions import ValidationError
        r = Ressource(
            type="produit", nom="Test", secteur=secteur,
            niveau_actuel=10, seuil_critique=50, seuil_alerte=20,
        )
        with pytest.raises(ValidationError):
            r.save()


@pytest.mark.django_db
class TestAPIRessource:
    def test_statut_non_modifiable_via_api(self, gerant, secteur):
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(
            "/api/v1/resources/",
            {
                "type": "produit", "nom": "Tomates en conserve", "secteur": secteur.id,
                "niveau_actuel": 60, "seuil_critique": 15, "seuil_alerte": 30,
                "statut": "stable",  # tentative d'injection — doit être ignorée et recalculée
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["statut"] == "stable"  # recalculé correctement (60 > 30), pas une simple acceptation de l'input

    def test_resume_par_statut(self, gerant, secteur):
        Ressource.objects.create(type="p", nom="A", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=20)
        Ressource.objects.create(type="p", nom="B", secteur=secteur, niveau_actuel=15, seuil_critique=10, seuil_alerte=20)
        Ressource.objects.create(type="p", nom="C", secteur=secteur, niveau_actuel=50, seuil_critique=10, seuil_alerte=20)
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get("/api/v1/resources/summary/")
        assert response.status_code == 200
        assert response.data == {"total": 3, "critiques": 1, "a_surveiller": 1, "stables": 1}

    def test_filtre_par_statut(self, gerant, secteur):
        Ressource.objects.create(type="p", nom="A", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=20)
        Ressource.objects.create(type="p", nom="B", secteur=secteur, niveau_actuel=50, seuil_critique=10, seuil_alerte=20)
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get("/api/v1/resources/", {"statut": "critique"})
        assert response.data["count"] == 1
        assert response.data["results"][0]["nom"] == "A"


@pytest.mark.django_db
class TestSeuilsRecommandes:
    def test_delai_jours_obligatoire(self, gerant, secteur):
        ressource = Ressource.objects.create(type="p", nom="Riz", secteur=secteur, niveau_actuel=100, seuil_critique=None, seuil_alerte=None)
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get(f"/api/v1/resources/{ressource.id}/seuils-recommandes/")
        assert response.status_code == 400
        assert "delai_jours" in response.data["champs_invalides"]

    def test_pas_calculable_sans_historique(self, gerant, secteur):
        ressource = Ressource.objects.create(type="p", nom="Riz", secteur=secteur, niveau_actuel=100)
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get(f"/api/v1/resources/{ressource.id}/seuils-recommandes/", {"delai_jours": "7"})
        assert response.status_code == 200
        assert response.data["calculable"] is False

    def test_calcul_base_sur_la_consommation_reelle(self, gerant, secteur):
        from apps.treasury.models import Transaction
        ressource = Ressource.objects.create(type="p", nom="Riz étuvé 25kg", secteur=secteur, niveau_actuel=100)
        maintenant = timezone.now()
        # 300 unités consommées sur 30 jours -> 10/jour en moyenne
        Transaction.objects.create(
            type="mouvement_stock", secteur=secteur, ressource=ressource,
            quantite=-300, date_transaction=maintenant - timedelta(days=5),
        )
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get(
            f"/api/v1/resources/{ressource.id}/seuils-recommandes/",
            {"delai_jours": "7", "facteur_securite": "2"},
        )
        assert response.status_code == 200
        assert response.data["calculable"] is True
        assert Decimal(response.data["consommation_moyenne_quotidienne"]) == Decimal("10.00")
        assert Decimal(response.data["seuil_critique_suggere"]) == Decimal("70.00")   # 10 * 7
        assert Decimal(response.data["seuil_alerte_suggere"]) == Decimal("140.00")    # 70 * 2

    def test_facteur_securite_par_defaut(self, gerant, secteur):
        from apps.treasury.models import Transaction
        ressource = Ressource.objects.create(type="p", nom="Riz", secteur=secteur, niveau_actuel=100)
        maintenant = timezone.now()
        Transaction.objects.create(
            type="mouvement_stock", secteur=secteur, ressource=ressource,
            quantite=-60, date_transaction=maintenant - timedelta(days=1),
        )
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get(f"/api/v1/resources/{ressource.id}/seuils-recommandes/", {"delai_jours": "5"})
        assert Decimal(response.data["facteur_securite_utilise"]) == Decimal("1.5")

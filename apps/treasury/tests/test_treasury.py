from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import Secteur
from apps.resources.models import Ressource
from apps.treasury.models import Facture, Transaction


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def role_gerant(db):
    role = Role.objects.create(nom="Gérant")
    for code in ["treasury.read", "treasury.write", "resources.read", "resources.write"]:
        module = code.split(".")[0]
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"module": module})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    return role


@pytest.fixture
def gerant(db, role_gerant, secteur):
    return Utilisateur.objects.create_user(
        email="gerant@spipme.com", nom_utilisateur="gerant1",
        password="MotDePasse#2026", role=role_gerant, secteur_principal=secteur,
    )


@pytest.fixture
def ressource(db, secteur):
    return Ressource.objects.create(
        type="produit", nom="Riz étuvé 25kg", secteur=secteur,
        niveau_actuel=100, seuil_critique=10, seuil_alerte=30,
    )


@pytest.mark.django_db
class TestModeleTransaction:
    def test_entree_sans_montant_est_rejetee(self, secteur):
        from django.core.exceptions import ValidationError
        t = Transaction(type="entree", secteur=secteur, date_transaction=timezone.now())
        with pytest.raises(ValidationError):
            t.save()

    def test_mouvement_stock_met_a_jour_le_niveau_ressource(self, secteur, ressource):
        Transaction.objects.create(
            type="mouvement_stock", secteur=secteur, ressource=ressource,
            quantite=-10, date_transaction=timezone.now(),
        )
        ressource.refresh_from_db()
        assert ressource.niveau_actuel == 90
        assert ressource.statut == "stable"  # 90 > seuil_alerte (30)

    def test_mouvement_stock_negatif_peut_faire_passer_en_critique(self, secteur, ressource):
        Transaction.objects.create(
            type="mouvement_stock", secteur=secteur, ressource=ressource,
            quantite=-95, date_transaction=timezone.now(),
        )
        ressource.refresh_from_db()
        assert ressource.niveau_actuel == 5
        assert ressource.statut == "critique"

    def test_mouvement_stock_sans_ressource_est_rejete(self, secteur):
        from django.core.exceptions import ValidationError
        t = Transaction(type="mouvement_stock", secteur=secteur, quantite=5, date_transaction=timezone.now())
        with pytest.raises(ValidationError):
            t.save()


@pytest.mark.django_db
class TestAPITransaction:
    def test_creation_entree_via_api(self, gerant, secteur):
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(
            "/api/v1/transactions/",
            {
                "type": "entree", "montant": "2450000", "secteur": secteur.id,
                "date_transaction": timezone.now().isoformat(),
                "description": "Vente de marchandises",
            },
            format="json",
        )
        assert response.status_code == 201
        assert response.data["auteur"] == gerant.id

    def test_resume_sans_donnees_reste_type_chaine_dans_le_json(self, gerant, secteur):
        """
        Preuve que le contenu JSON brut est bien une chaîne ("0"), pas un
        nombre (0), même sans transaction — sinon un client strict (ex.
        kotlinx.serialization côté Android) échouerait le parsing selon
        qu'il y a des données ou non. Vérifie le JSON brut, pas
        `response.data` (déjà re-parsé côté Python, où la distinction
        str/int serait invisible).
        """
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get("/api/v1/transactions/summary/")
        assert response.status_code == 200
        assert b'"entrees_mois":"0"' in response.content or b'"entrees_mois": "0"' in response.content

    def test_resume_mensuel(self, gerant, secteur):
        client = APIClient()
        client.force_authenticate(user=gerant)
        maintenant = timezone.now()
        Transaction.objects.create(type="entree", montant=12450000, secteur=secteur, date_transaction=maintenant)
        Transaction.objects.create(type="sortie", montant=8230000, secteur=secteur, date_transaction=maintenant)
        response = client.get("/api/v1/transactions/summary/")
        assert response.status_code == 200
        assert Decimal(response.data["entrees_mois"]) == 12450000
        assert Decimal(response.data["sorties_mois"]) == 8230000
        assert Decimal(response.data["solde_net_mois"]) == 4220000

    def test_evolution_du_solde(self, gerant, secteur):
        client = APIClient()
        client.force_authenticate(user=gerant)
        Transaction.objects.create(
            type="entree", montant=1000, secteur=secteur,
            date_transaction=timezone.now() - timedelta(days=3),
        )
        response = client.get("/api/v1/transactions/evolution/", {"jours": 7})
        assert response.status_code == 200
        assert len(response.data) == 7
        # Le solde du dernier point (aujourd'hui) doit refléter l'entrée cumulée
        assert Decimal(response.data[-1]["solde"]) == 1000
        # Un point antérieur à l'entrée doit être à 0
        assert Decimal(response.data[0]["solde"]) == 0


@pytest.mark.django_db
class TestEvolutionStockDepuisResources:
    def test_evolution_ressource_reconstitue_lhistorique(self, gerant, secteur, ressource):
        client = APIClient()
        client.force_authenticate(user=gerant)
        Transaction.objects.create(
            type="mouvement_stock", secteur=secteur, ressource=ressource,
            quantite=-10, date_transaction=timezone.now() - timedelta(days=1),
        )
        ressource.refresh_from_db()
        assert ressource.niveau_actuel == 90

        response = client.get(f"/api/v1/resources/{ressource.id}/evolution/", {"jours": 3})
        assert response.status_code == 200
        assert len(response.data) == 3
        niveaux = {p["date"]: p["niveau"] for p in response.data}
        aujourdhui = timezone.now().date().isoformat()
        hier = (timezone.now().date() - timedelta(days=1)).isoformat()
        avant_hier = (timezone.now().date() - timedelta(days=2)).isoformat()
        assert Decimal(niveaux[aujourdhui]) == 90
        assert Decimal(niveaux[hier]) == 90
        assert Decimal(niveaux[avant_hier]) == 100  # avant le mouvement de -10


@pytest.mark.django_db
class TestFacture:
    def test_creation_facture(self, gerant, secteur):
        from apps.registry.models import Entite
        entite = Entite.objects.create(type="client", nom="Dupont SARL", secteur=secteur)
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(
            "/api/v1/invoices/",
            {"numero": "FAC-2026-0452", "entite": entite.id, "montant": "125000", "secteur": secteur.id},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["statut"] == "emise"

    def test_tva_non_renseignee_ne_change_rien(self, secteur):
        from apps.registry.models import Entite
        entite = Entite.objects.create(type="client", nom="Client", secteur=secteur)
        facture = Facture.objects.create(numero="FAC-A", entite=entite, montant=Decimal("100000"), secteur=secteur)
        assert facture.montant_tva is None
        assert facture.montant_ttc == Decimal("100000")

    def test_tva_calculee_a_partir_du_taux_fourni_par_lutilisateur(self, secteur):
        from apps.registry.models import Entite
        entite = Entite.objects.create(type="client", nom="Client", secteur=secteur)
        facture = Facture.objects.create(
            numero="FAC-B", entite=entite, montant=Decimal("100000"), taux_tva=Decimal("18"), secteur=secteur,
        )
        assert facture.montant_tva == Decimal("18000.00")
        assert facture.montant_ttc == Decimal("118000.00")

    def test_creation_facture_avec_tva_via_api(self, gerant, secteur):
        from apps.registry.models import Entite
        entite = Entite.objects.create(type="client", nom="Client API", secteur=secteur)
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(
            "/api/v1/invoices/",
            {"numero": "FAC-C", "entite": entite.id, "montant": "50000", "taux_tva": "18", "secteur": secteur.id},
            format="json",
        )
        assert response.status_code == 201
        assert Decimal(response.data["montant_tva"]) == Decimal("9000.00")

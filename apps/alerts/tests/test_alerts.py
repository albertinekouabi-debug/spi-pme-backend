from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.alerts import services
from apps.alerts.detectors import (
    ConsommationEleveeDetecteur,
    FactureImpayeeDetecteur,
    StockASurveillerDetecteur,
    StockCritiqueDetecteur,
    TacheEnRetardDetecteur,
)
from apps.alerts.models import Alerte
from apps.core.models import Secteur
from apps.registry.models import Entite
from apps.resources.models import Ressource
from apps.tasks.models import Tache
from apps.treasury.models import Facture, Transaction


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def role_gerant(db):
    role = Role.objects.create(nom="Gérant")
    for code in ["alerts.read", "alerts.write"]:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"module": "alerts"})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    return role


@pytest.fixture
def gerant(db, role_gerant, secteur):
    return Utilisateur.objects.create_user(
        email="gerant@spipme.com", nom_utilisateur="gerant1",
        password="MotDePasse#2026", role=role_gerant, secteur_principal=secteur,
    )


@pytest.mark.django_db
class TestDetecteurs:
    def test_stock_critique(self, secteur):
        r = Ressource.objects.create(type="p", nom="Lait en poudre", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=25)
        drafts = StockCritiqueDetecteur().detecter(secteur)
        assert len(drafts) == 1 and drafts[0].ressource_id == r.id

    def test_stock_a_surveiller(self, secteur):
        r = Ressource.objects.create(type="p", nom="Riz étuvé 25kg", secteur=secteur, niveau_actuel=18, seuil_critique=10, seuil_alerte=50)
        drafts = StockASurveillerDetecteur().detecter(secteur)
        assert len(drafts) == 1 and drafts[0].niveau == "elevee"

    def test_facture_impayee_niveau_selon_le_retard(self, secteur):
        entite = Entite.objects.create(type="client", nom="Dupont SARL", secteur=secteur)
        aujourdhui = timezone.now().date()
        Facture.objects.create(
            numero="FAC-1", entite=entite, montant=Decimal("125000"), secteur=secteur,
            statut="impayee", date_echeance=aujourdhui - timedelta(days=35),
        )
        Facture.objects.create(
            numero="FAC-2", entite=entite, montant=Decimal("50000"), secteur=secteur,
            statut="impayee", date_echeance=aujourdhui - timedelta(days=5),
        )
        drafts = {d.description[:0] or d.titre: d for d in FactureImpayeeDetecteur().detecter(secteur)}
        niveaux = sorted(d.niveau for d in FactureImpayeeDetecteur().detecter(secteur))
        assert niveaux == ["elevee", "moderee"]

    def test_facture_payee_nest_pas_detectee(self, secteur):
        entite = Entite.objects.create(type="client", nom="Client OK", secteur=secteur)
        Facture.objects.create(
            numero="FAC-3", entite=entite, montant=Decimal("10000"), secteur=secteur,
            statut="payee", date_echeance=timezone.now().date() - timedelta(days=10),
        )
        assert FactureImpayeeDetecteur().detecter(secteur) == []

    def test_tache_en_retard(self, secteur):
        hier = timezone.now().date() - timedelta(days=1)
        Tache.objects.create(titre="Commander du stock", statut="a_faire", echeance=hier, secteur=secteur)
        drafts = TacheEnRetardDetecteur().detecter(secteur)
        assert len(drafts) == 1

    def test_consommation_elevee_necessite_deux_semaines_dhistorique(self, secteur):
        ressource = Ressource.objects.create(type="p", nom="X", secteur=secteur, niveau_actuel=100, seuil_critique=5, seuil_alerte=10)
        maintenant = timezone.now()
        Transaction.objects.create(type="mouvement_stock", secteur=secteur, ressource=ressource, quantite=-50, date_transaction=maintenant - timedelta(days=2))
        # Aucune donnée la semaine précédente -> pas de signal (évite un faux positif de démarrage)
        assert ConsommationEleveeDetecteur().detecter(secteur) == []

    def test_consommation_elevee_detectee_si_hausse_significative(self, secteur):
        ressource = Ressource.objects.create(type="p", nom="X", secteur=secteur, niveau_actuel=100, seuil_critique=5, seuil_alerte=10)
        maintenant = timezone.now()
        Transaction.objects.create(type="mouvement_stock", secteur=secteur, ressource=ressource, quantite=-100, date_transaction=maintenant - timedelta(days=2))
        Transaction.objects.create(type="mouvement_stock", secteur=secteur, ressource=ressource, quantite=-50, date_transaction=maintenant - timedelta(days=9))
        drafts = ConsommationEleveeDetecteur().detecter(secteur)
        assert len(drafts) == 1
        assert "100%" in drafts[0].description  # 100 vs 50 = +100%


@pytest.mark.django_db
class TestReconciliation:
    def test_generation_cree_une_alerte(self, secteur):
        Ressource.objects.create(type="p", nom="Lait en poudre", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=25)
        resultat = services.generer_pour_secteur(secteur)
        assert len(resultat["creees"]) == 1
        assert Alerte.objects.filter(statut="active").count() == 1

    def test_generation_est_idempotente(self, secteur):
        Ressource.objects.create(type="p", nom="Lait en poudre", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=25)
        services.generer_pour_secteur(secteur)
        resultat = services.generer_pour_secteur(secteur)
        assert len(resultat["creees"]) == 0  # déjà une alerte active pour cette ressource
        assert Alerte.objects.filter(statut="active").count() == 1

    def test_resolution_automatique_quand_la_condition_disparait(self, secteur):
        ressource = Ressource.objects.create(type="p", nom="Lait en poudre", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=25)
        services.generer_pour_secteur(secteur)
        assert Alerte.objects.filter(statut="active").count() == 1

        ressource.niveau_actuel = 100
        ressource.save()  # statut recalculé -> stable

        resultat = services.generer_pour_secteur(secteur)
        assert len(resultat["resolues_automatiquement"]) == 1
        assert Alerte.objects.filter(statut="active").count() == 0
        assert Alerte.objects.filter(statut="traitee").count() == 1


@pytest.mark.django_db
class TestActionsManuelles:
    def test_resolve_et_double_resolve(self, secteur):
        Ressource.objects.create(type="p", nom="Lait en poudre", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=25)
        alerte = services.generer_pour_secteur(secteur)["creees"][0]
        services.traiter_alerte(alerte.id)
        with pytest.raises(services.DecisionAlerteError):
            services.traiter_alerte(alerte.id)

    def test_ignore(self, secteur):
        Ressource.objects.create(type="p", nom="Lait en poudre", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=25)
        alerte = services.generer_pour_secteur(secteur)["creees"][0]
        resultat = services.ignorer_alerte(alerte.id)
        assert resultat.statut == "ignoree"


@pytest.mark.django_db
class TestAPIAlerte:
    def test_lecture_seule_pas_de_patch_generique(self, secteur, gerant):
        Ressource.objects.create(type="p", nom="Lait en poudre", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=25)
        alerte = services.generer_pour_secteur(secteur)["creees"][0]
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.patch(f"/api/v1/alerts/{alerte.id}/", {"statut": "traitee"}, format="json")
        assert response.status_code == 405

    def test_generate_puis_resolve_via_api(self, secteur, gerant):
        Ressource.objects.create(type="p", nom="Lait en poudre", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=25)
        client = APIClient()
        client.force_authenticate(user=gerant)
        reponse_generation = client.post("/api/v1/alerts/generate/", {"secteur": secteur.id}, format="json")
        assert reponse_generation.status_code == 201
        alerte_id = reponse_generation.data["creees"][0]["id"]

        reponse_resolve = client.post(f"/api/v1/alerts/{alerte_id}/resolve/")
        assert reponse_resolve.status_code == 200
        assert reponse_resolve.data["statut"] == "traitee"

    def test_summary(self, secteur, gerant):
        Ressource.objects.create(type="p", nom="Lait en poudre", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=25)
        Ressource.objects.create(type="p", nom="Riz étuvé 25kg", secteur=secteur, niveau_actuel=18, seuil_critique=10, seuil_alerte=50)
        services.generer_pour_secteur(secteur)
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get("/api/v1/alerts/summary/")
        assert response.status_code == 200
        assert response.data["critiques"] == 1
        assert response.data["elevees"] == 1

    def test_employe_sans_permission_est_rejete(self, secteur):
        role_employe = Role.objects.create(nom="Employé")
        employe = Utilisateur.objects.create_user(
            email="employe@spipme.com", nom_utilisateur="employe1",
            password="MotDePasse#2026", role=role_employe, secteur_principal=secteur,
        )
        client = APIClient()
        client.force_authenticate(user=employe)
        response = client.get("/api/v1/alerts/")
        assert response.status_code == 403

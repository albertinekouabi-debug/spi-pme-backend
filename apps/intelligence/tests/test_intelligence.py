from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import Secteur
from apps.intelligence import services
from apps.intelligence.algorithms.seuil import SeuilAlgorithme
from apps.intelligence.algorithms.tendance import TendanceAlgorithme
from apps.intelligence.models import Suggestion
from apps.resources.models import Ressource
from apps.treasury.models import Transaction


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def role_gerant(db):
    role = Role.objects.create(nom="Gérant")
    for code in ["intelligence.read", "intelligence.write"]:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"module": "intelligence"})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    return role


@pytest.fixture
def gerant(db, role_gerant, secteur):
    return Utilisateur.objects.create_user(
        email="gerant@spipme.com", nom_utilisateur="gerant1",
        password="MotDePasse#2026", role=role_gerant, secteur_principal=secteur,
    )


@pytest.fixture
def ressource_critique(db, secteur):
    return Ressource.objects.create(
        type="produit", nom="Lait en poudre", secteur=secteur, unite="unités",
        niveau_actuel=2, seuil_critique=10, seuil_alerte=25, valeur_unitaire=Decimal("5500"),
    )


@pytest.mark.django_db
class TestAlgorithmeSeuil:
    def test_genere_une_suggestion_pour_ressource_critique(self, secteur, ressource_critique):
        drafts = SeuilAlgorithme().generer(secteur)
        assert len(drafts) == 1
        draft = drafts[0]
        assert draft.ressource_liee_id == ressource_critique.id
        assert draft.facteurs["quantite_suggeree"] == 23.0  # seuil_alerte(25) - niveau(2)
        assert draft.impact_estime == Decimal("126500.00")  # 23 * 5500
        assert draft.confiance >= Decimal("80")

    def test_pas_de_suggestion_pour_ressource_stable(self, secteur):
        Ressource.objects.create(
            type="produit", nom="Huile 5L", secteur=secteur,
            niveau_actuel=120, seuil_critique=10, seuil_alerte=20,
        )
        assert SeuilAlgorithme().generer(secteur) == []

    def test_impact_estime_absent_sans_valeur_unitaire(self, secteur):
        Ressource.objects.create(
            type="lit", nom="Lit disponible", secteur=secteur,
            niveau_actuel=1, seuil_critique=5, seuil_alerte=10,  # valeur_unitaire non renseignée
        )
        drafts = SeuilAlgorithme().generer(secteur)
        assert drafts[0].impact_estime is None


@pytest.mark.django_db
class TestAlgorithmeTendance:
    def test_detecte_une_tendance_baissiere_et_projette_la_rupture(self, secteur):
        ressource = Ressource.objects.create(
            type="produit", nom="Riz étuvé 25kg", secteur=secteur,
            niveau_actuel=15, seuil_critique=10, seuil_alerte=50,
        )
        maintenant = timezone.now()
        for i, quantite in enumerate([-10, -10, -10, -10]):
            Transaction.objects.create(
                type="mouvement_stock", secteur=secteur, ressource=ressource,
                quantite=quantite, date_transaction=maintenant - timedelta(days=3 - i),
            )
        drafts = TendanceAlgorithme().generer(secteur)
        assert len(drafts) == 1
        assert drafts[0].ressource_liee_id == ressource.id
        assert drafts[0].facteurs["pente_quotidienne"] < 0

    def test_pas_dalerte_si_tendance_stable_ou_haussiere(self, secteur):
        ressource = Ressource.objects.create(
            type="produit", nom="Sucre 50kg", secteur=secteur,
            niveau_actuel=200, seuil_critique=10, seuil_alerte=50,
        )
        maintenant = timezone.now()
        for i, quantite in enumerate([10, 10, 10]):
            Transaction.objects.create(
                type="mouvement_stock", secteur=secteur, ressource=ressource,
                quantite=quantite, date_transaction=maintenant - timedelta(days=2 - i),
            )
        assert TendanceAlgorithme().generer(secteur) == []

    def test_pas_dalerte_si_rupture_trop_lointaine(self, secteur):
        # Baisse très lente : niveau élevé, faible pente -> rupture projetée bien au-delà de 7 jours
        ressource = Ressource.objects.create(
            type="produit", nom="Farine 50kg", secteur=secteur,
            niveau_actuel=1000, seuil_critique=10, seuil_alerte=50,
        )
        maintenant = timezone.now()
        for i, quantite in enumerate([-1, -1, -1]):
            Transaction.objects.create(
                type="mouvement_stock", secteur=secteur, ressource=ressource,
                quantite=quantite, date_transaction=maintenant - timedelta(days=2 - i),
            )
        assert TendanceAlgorithme().generer(secteur) == []


@pytest.mark.django_db
class TestServiceGeneration:
    def test_generation_est_idempotente(self, secteur, ressource_critique):
        premiere_passe = services.generer_pour_secteur(secteur)
        deuxieme_passe = services.generer_pour_secteur(secteur)
        assert len(premiere_passe) == 1
        assert len(deuxieme_passe) == 0  # déjà une suggestion en_attente pour cette ressource
        assert Suggestion.objects.count() == 1

    def test_nouvelle_generation_possible_apres_decision(self, secteur, ressource_critique, gerant):
        suggestions = services.generer_pour_secteur(secteur)
        services.rejeter_suggestion(suggestions[0].id, decideur=gerant, motif="Fournisseur en rupture aussi.")
        nouvelle_passe = services.generer_pour_secteur(secteur)
        assert len(nouvelle_passe) == 1  # la précédente n'est plus en_attente, donc pas de blocage


@pytest.mark.django_db
class TestValidationEtRejet:
    def test_validation_cree_une_transaction_et_met_a_jour_le_stock(self, secteur, ressource_critique, gerant):
        suggestion = services.generer_pour_secteur(secteur)[0]
        resultat = services.valider_suggestion(suggestion.id, decideur=gerant)

        assert resultat.statut == "validee"
        assert resultat.transaction_resultante is not None
        assert resultat.transaction_resultante.type == "mouvement_stock"

        ressource_critique.refresh_from_db()
        assert ressource_critique.niveau_actuel == Decimal("25")  # 2 + 23
        assert ressource_critique.statut == "a_surveiller"  # 25 <= seuil_alerte(25)

    def test_double_validation_echoue(self, secteur, ressource_critique, gerant):
        suggestion = services.generer_pour_secteur(secteur)[0]
        services.valider_suggestion(suggestion.id, decideur=gerant)
        with pytest.raises(services.DecisionSuggestionError):
            services.valider_suggestion(suggestion.id, decideur=gerant)
        # Une seule Transaction créée malgré la double tentative
        assert Transaction.objects.filter(ressource=ressource_critique).count() == 1

    def test_rejet_exige_un_motif_via_api(self, secteur, ressource_critique, gerant):
        suggestion = services.generer_pour_secteur(secteur)[0]
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(f"/api/v1/suggestions/{suggestion.id}/reject/", {}, format="json")
        assert response.status_code == 400

    def test_rejet_ne_cree_aucune_transaction(self, secteur, ressource_critique, gerant):
        suggestion = services.generer_pour_secteur(secteur)[0]
        services.rejeter_suggestion(suggestion.id, decideur=gerant, motif="Stock suffisant en réalité.")
        suggestion.refresh_from_db()
        assert suggestion.statut == "rejetee"
        assert suggestion.transaction_resultante is None
        assert Transaction.objects.count() == 0


@pytest.mark.django_db
class TestAPISuggestion:
    def test_lecture_seule_pas_de_patch_generique(self, secteur, ressource_critique, gerant):
        suggestion = services.generer_pour_secteur(secteur)[0]
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.patch(f"/api/v1/suggestions/{suggestion.id}/", {"statut": "validee"}, format="json")
        assert response.status_code == 405  # méthode non autorisée : pas de route PATCH sur ce ViewSet

    def test_generate_via_api_puis_validate(self, secteur, ressource_critique, gerant):
        client = APIClient()
        client.force_authenticate(user=gerant)
        reponse_generation = client.post("/api/v1/suggestions/generate/", {"secteur": secteur.id}, format="json")
        assert reponse_generation.status_code == 201
        suggestion_id = reponse_generation.data[0]["id"]

        reponse_validation = client.post(f"/api/v1/suggestions/{suggestion_id}/validate/")
        assert reponse_validation.status_code == 200
        assert reponse_validation.data["statut"] == "validee"
        assert reponse_validation.data["decideur"] == gerant.id

    def test_summary(self, secteur, ressource_critique, gerant):
        suggestion = services.generer_pour_secteur(secteur)[0]
        services.valider_suggestion(suggestion.id, decideur=gerant)
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get("/api/v1/suggestions/summary/")
        assert response.status_code == 200
        assert response.data["total"] == 1
        assert response.data["validees"] == 1
        assert response.data["taux_acceptation"] == 100.0

    def test_employe_sans_permission_est_rejete(self, secteur):
        role_employe = Role.objects.create(nom="Employé")
        employe = Utilisateur.objects.create_user(
            email="employe@spipme.com", nom_utilisateur="employe1",
            password="MotDePasse#2026", role=role_employe, secteur_principal=secteur,
        )
        client = APIClient()
        client.force_authenticate(user=employe)
        response = client.get("/api/v1/suggestions/")
        assert response.status_code == 403

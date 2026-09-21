from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import ParametreSysteme
from apps.registry.models import Entite
from apps.treasury import compliance
from apps.treasury.models import DeclarationConformite, Transaction
from apps.core.models import Secteur


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def role_gerant(db):
    role = Role.objects.create(nom="Gérant")
    for code in ["treasury.read", "treasury.write"]:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"module": "treasury"})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    return role


@pytest.fixture
def gerant(db, role_gerant, secteur):
    return Utilisateur.objects.create_user(
        email="gerant@spipme.com", nom_utilisateur="gerant1",
        password="MotDePasse#2026", role=role_gerant, secteur_principal=secteur,
    )


@pytest.fixture
def seuil_configure(db):
    return ParametreSysteme.objects.create(
        cle=compliance.CLE_SEUIL_ESPECES, secteur=None,
        valeur={"montant": 5_000_000, "devise": "XOF"},
    )


@pytest.mark.django_db
class TestSeuilConfigurable:
    def test_aucun_seuil_configure_ne_declenche_rien(self, secteur):
        # Pas de fixture seuil_configure : la table ParametreSysteme est vide.
        assert compliance.obtenir_seuil_especes() is None
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("50000000"), mode_paiement="especes",
            secteur=secteur, date_transaction=timezone.now(),
        )
        assert compliance.evaluer_transaction(transaction) is None
        assert DeclarationConformite.objects.count() == 0

    def test_le_seuil_est_bien_lu_depuis_la_configuration_pas_code_en_dur(self, secteur, seuil_configure):
        # Preuve que rien n'est figé dans le code : changer la configuration
        # change le comportement, sans toucher à une seule ligne de Python.
        assert compliance.obtenir_seuil_especes() == Decimal("5000000")

        seuil_configure.valeur = {"montant": 1_000_000, "devise": "XOF"}
        seuil_configure.save()
        assert compliance.obtenir_seuil_especes() == Decimal("1000000")

        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("2000000"), mode_paiement="especes",
            secteur=secteur, date_transaction=timezone.now(),
        )
        # Sous l'ancien seuil (5M) cette transaction n'aurait pas été détectée ;
        # avec le nouveau seuil configuré (1M), elle l'est.
        declaration = compliance.evaluer_transaction(transaction)
        assert declaration is not None
        assert declaration.seuil_applique == Decimal("1000000")


@pytest.mark.django_db
class TestDetectionAutomatique:
    def test_especes_au_dela_du_seuil_declenche_une_declaration(self, secteur, seuil_configure):
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("6000000"), mode_paiement="especes",
            secteur=secteur, date_transaction=timezone.now(),
        )
        declaration = compliance.evaluer_transaction(transaction)
        assert declaration is not None
        assert declaration.motif == "especes_superieur_seuil"
        assert declaration.statut == "a_declarer"

    def test_especes_sous_le_seuil_ne_declenche_rien(self, secteur, seuil_configure):
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("500000"), mode_paiement="especes",
            secteur=secteur, date_transaction=timezone.now(),
        )
        assert compliance.evaluer_transaction(transaction) is None

    def test_virement_au_dela_du_seuil_ne_declenche_rien(self, secteur, seuil_configure):
        # La règle vise spécifiquement les paiements en espèces.
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("10000000"), mode_paiement="virement",
            secteur=secteur, date_transaction=timezone.now(),
        )
        assert compliance.evaluer_transaction(transaction) is None

    def test_mouvement_stock_nest_jamais_concerne(self, secteur, seuil_configure):
        from apps.resources.models import Ressource
        ressource = Ressource.objects.create(type="p", nom="X", secteur=secteur, niveau_actuel=1000)
        transaction = Transaction.objects.create(
            type="mouvement_stock", quantite=Decimal("-10000000"), ressource=ressource,
            secteur=secteur, date_transaction=timezone.now(),
        )
        assert compliance.evaluer_transaction(transaction) is None

    def test_idempotence_pas_de_double_declaration(self, secteur, seuil_configure):
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("6000000"), mode_paiement="especes",
            secteur=secteur, date_transaction=timezone.now(),
        )
        premiere = compliance.evaluer_transaction(transaction)
        transaction.refresh_from_db()
        deuxieme = compliance.evaluer_transaction(transaction)
        assert premiere is not None
        assert deuxieme is None
        assert DeclarationConformite.objects.filter(transaction=transaction).count() == 1

    def test_declenchement_automatique_via_api(self, gerant, secteur, seuil_configure):
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(
            "/api/v1/transactions/",
            {
                "type": "sortie", "montant": "6000000", "mode_paiement": "especes",
                "secteur": secteur.id, "date_transaction": timezone.now().isoformat(),
            },
            format="json",
        )
        assert response.status_code == 201
        assert DeclarationConformite.objects.filter(transaction_id=response.data["id"]).exists()


@pytest.mark.django_db
class TestSignalementManuel:
    def test_signalement_quel_que_soit_le_montant(self, secteur, seuil_configure):
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("10000"), mode_paiement="virement",  # bien en dessous du seuil
            secteur=secteur, date_transaction=timezone.now(),
        )
        declaration = compliance.signaler_manuellement(transaction.id, note="Bénéficiaire inconnu, comportement inhabituel.")
        assert declaration.motif == "signalement_manuel"

    def test_double_signalement_refuse(self, secteur, seuil_configure):
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("10000"), secteur=secteur, date_transaction=timezone.now(),
        )
        compliance.signaler_manuellement(transaction.id, note="Motif 1")
        with pytest.raises(compliance.DeclarationConformiteError):
            compliance.signaler_manuellement(transaction.id, note="Motif 2")


@pytest.mark.django_db
class TestActionsDecision:
    def test_declarer_exige_une_reference(self, gerant, secteur, seuil_configure):
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("6000000"), mode_paiement="especes",
            secteur=secteur, date_transaction=timezone.now(),
        )
        declaration = compliance.evaluer_transaction(transaction)
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(f"/api/v1/compliance-declarations/{declaration.id}/declare/", {}, format="json")
        assert response.status_code == 400

    def test_declarer_avec_reference(self, gerant, secteur, seuil_configure):
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("6000000"), mode_paiement="especes",
            secteur=secteur, date_transaction=timezone.now(),
        )
        declaration = compliance.evaluer_transaction(transaction)
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(
            f"/api/v1/compliance-declarations/{declaration.id}/declare/",
            {"reference_declaration": "ANIF-2026-00123"}, format="json",
        )
        assert response.status_code == 200
        assert response.data["statut"] == "declaree"

    def test_double_decision_refusee(self, secteur, seuil_configure, gerant):
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("6000000"), mode_paiement="especes",
            secteur=secteur, date_transaction=timezone.now(),
        )
        declaration = compliance.evaluer_transaction(transaction)
        compliance.marquer_declaree(declaration.id, declarant=gerant, reference_declaration="ANIF-1")
        with pytest.raises(compliance.DeclarationConformiteError):
            compliance.marquer_declaree(declaration.id, declarant=gerant, reference_declaration="ANIF-2")

    def test_exempter(self, gerant, secteur, seuil_configure):
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("6000000"), mode_paiement="especes",
            secteur=secteur, date_transaction=timezone.now(),
        )
        declaration = compliance.evaluer_transaction(transaction)
        resultat = compliance.marquer_exemptee(declaration.id, declarant=gerant, note="Virement interne entre comptes de la même entreprise.")
        assert resultat.statut == "exemptee"

    def test_employe_sans_permission_est_rejete(self, secteur, seuil_configure):
        role_employe = Role.objects.create(nom="Employé")
        employe = Utilisateur.objects.create_user(
            email="employe@spipme.com", nom_utilisateur="employe1",
            password="MotDePasse#2026", role=role_employe, secteur_principal=secteur,
        )
        client = APIClient()
        client.force_authenticate(user=employe)
        response = client.get("/api/v1/compliance-declarations/")
        assert response.status_code == 403

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.audit.models import JournalAudit
from apps.audit.services import enregistrer
from apps.core.models import ParametreSysteme, Secteur
from apps.intelligence import services as intelligence_services
from apps.resources.models import Ressource
from apps.treasury import compliance


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def role_auditeur(db):
    role = Role.objects.create(nom="Auditeur")
    perm, _ = Permission.objects.get_or_create(code="audit.read", defaults={"module": "audit"})
    RolePermission.objects.get_or_create(role=role, permission=perm)
    return role


@pytest.fixture
def auditeur(db, role_auditeur, secteur):
    return Utilisateur.objects.create_user(
        email="auditeur@spipme.com", nom_utilisateur="auditeur1",
        password="MotDePasse#2026", role=role_auditeur, secteur_principal=secteur,
    )


@pytest.mark.django_db
class TestLectureSeuleORM:
    def test_impossible_de_modifier_une_entree_existante(self, secteur):
        entree = enregistrer(action="test", module="test", resultat="reussi")
        entree.resultat = "echec"
        with pytest.raises(ValidationError):
            entree.save()

    def test_impossible_de_supprimer_une_entree(self, secteur):
        entree = enregistrer(action="test", module="test", resultat="reussi")
        with pytest.raises(ValidationError):
            entree.delete()
        assert JournalAudit.objects.filter(pk=entree.pk).exists()


@pytest.mark.django_db
class TestAPIAudit:
    def test_lecture_seule_pas_de_post_generique(self, auditeur):
        client = APIClient()
        client.force_authenticate(user=auditeur)
        response = client.post("/api/v1/audit-log/", {"action": "x", "module": "y"}, format="json")
        assert response.status_code == 405

    def test_filtre_par_module(self, auditeur):
        enregistrer(action="a", module="accounts")
        enregistrer(action="b", module="treasury")
        client = APIClient()
        client.force_authenticate(user=auditeur)
        response = client.get("/api/v1/audit-log/", {"module": "accounts"})
        assert response.data["count"] == 1

    def test_summary(self, auditeur):
        enregistrer(action="a", module="x", resultat="reussi")
        enregistrer(action="b", module="x", resultat="echec")
        enregistrer(action="c", module="x", resultat="avertissement")
        client = APIClient()
        client.force_authenticate(user=auditeur)
        response = client.get("/api/v1/audit-log/summary/")
        assert response.data == {"total": 3, "reussies": 1, "avertissements": 1, "echecs": 1}

    def test_gerant_sans_permission_audit_est_rejete(self, secteur):
        role_gerant = Role.objects.create(nom="Gérant")  # pas de permission audit.read, cf. seed_rbac réel
        gerant = Utilisateur.objects.create_user(
            email="gerant@spipme.com", nom_utilisateur="gerant1",
            password="MotDePasse#2026", role=role_gerant, secteur_principal=secteur,
        )
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get("/api/v1/audit-log/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestIntegrationReelle:
    """Vérifie que les modules précédents écrivent réellement dans le journal, pas seulement l'API qui le lit."""

    def test_connexion_reussie_est_journalisee(self, secteur):
        role = Role.objects.create(nom="Gérant")
        Utilisateur.objects.create_user(
            email="marie@spipme.com", nom_utilisateur="marie", password="MotDePasse#2026",
            role=role, secteur_principal=secteur,
        )
        client = APIClient()
        client.post("/api/v1/auth/login", {"identifiant": "marie", "password": "MotDePasse#2026"}, format="json")
        assert JournalAudit.objects.filter(module="accounts", action="connexion", resultat="reussi").exists()

    def test_connexion_echouee_est_journalisee(self, secteur):
        client = APIClient()
        client.post("/api/v1/auth/login", {"identifiant": "inconnu", "password": "x"}, format="json")
        assert JournalAudit.objects.filter(module="accounts", action="connexion", resultat="echec").exists()

    def test_validation_suggestion_est_journalisee(self, secteur):
        role = Role.objects.create(nom="Gérant")
        gerant = Utilisateur.objects.create_user(
            email="g@spipme.com", nom_utilisateur="g1", password="x", role=role, secteur_principal=secteur,
        )
        Ressource.objects.create(
            type="p", nom="Lait en poudre", secteur=secteur, unite="unités",
            niveau_actuel=2, seuil_critique=10, seuil_alerte=25, valeur_unitaire=Decimal("5500"),
        )
        suggestion = intelligence_services.generer_pour_secteur(secteur)[0]
        intelligence_services.valider_suggestion(suggestion.id, decideur=gerant)
        assert JournalAudit.objects.filter(module="intelligence", action="validation_suggestion", auteur=gerant).exists()

    def test_declaration_conformite_est_journalisee(self, secteur):
        from apps.treasury.models import Transaction
        role = Role.objects.create(nom="Gérant")
        gerant = Utilisateur.objects.create_user(
            email="g2@spipme.com", nom_utilisateur="g2", password="x", role=role, secteur_principal=secteur,
        )
        ParametreSysteme.objects.create(
            cle=compliance.CLE_SEUIL_ESPECES, secteur=None, valeur={"montant": 5_000_000, "devise": "XOF"},
        )
        transaction = Transaction.objects.create(
            type="sortie", montant=Decimal("6000000"), mode_paiement="especes",
            secteur=secteur, date_transaction=timezone.now(),
        )
        declaration = compliance.evaluer_transaction(transaction)
        compliance.marquer_declaree(declaration.id, declarant=gerant, reference_declaration="ANIF-2026-001")
        assert JournalAudit.objects.filter(
            module="treasury", action="declaration_conformite_effectuee", auteur=gerant
        ).exists()

    def test_resolution_alerte_est_journalisee(self, secteur):
        from apps.alerts import services as alerts_services
        role = Role.objects.create(nom="Gérant")
        gerant = Utilisateur.objects.create_user(
            email="g3@spipme.com", nom_utilisateur="g3", password="x", role=role, secteur_principal=secteur,
        )
        Ressource.objects.create(type="p", nom="Lait en poudre", secteur=secteur, niveau_actuel=2, seuil_critique=10, seuil_alerte=25)
        alerte = alerts_services.generer_pour_secteur(secteur)["creees"][0]
        alerts_services.traiter_alerte(alerte.id, utilisateur=gerant)
        assert JournalAudit.objects.filter(module="alerts", action="alerte_traitee", auteur=gerant).exists()

    def test_import_est_journalise(self, secteur):
        from apps.imports import services as imports_services
        contenu = b"Nom,Categorie,Quantite\nSucre 50kg,Epicerie,85\n"
        imports_services.importer("stock.csv", contenu, secteur, auteur=None)
        assert JournalAudit.objects.filter(module="imports", action="import_fichier").exists()

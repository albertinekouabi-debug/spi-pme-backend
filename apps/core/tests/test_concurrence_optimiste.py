"""LOT C — contrôle de version pour la synchronisation hors ligne (If-Match / If-Unmodified-Since)."""
from datetime import timedelta
from email.utils import format_datetime

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import Secteur
from apps.registry.models import Entite
from apps.resources.models import Ressource
from apps.treasury.models import Facture


@pytest.fixture
def ctx(db):
    secteur = Secteur.objects.create(code="commerce", nom="Commerce")
    role = Role.objects.create(nom="Gérant")
    for module in ("resources", "registry", "tasks", "treasury"):
        for action in ("read", "write"):
            p, _ = Permission.objects.get_or_create(code=f"{module}.{action}", defaults={"module": module})
            RolePermission.objects.get_or_create(role=role, permission=p)
    user = Utilisateur.objects.create_user(email="g@x.com", nom_utilisateur="g", password="MotDePasse#2026",
                                           role=role, secteur_principal=secteur)
    c = APIClient(); c.force_authenticate(user=user)
    r = Ressource.objects.create(type="produit", nom="Riz", secteur=secteur, niveau_actuel=50,
                                 seuil_critique=5, seuil_alerte=10)
    return c, secteur, r


@pytest.mark.django_db
class TestIfMatch:
    def test_la_version_s_incremente_a_chaque_modification_et_etag_expose(self, ctx):
        c, _, r = ctx
        assert r.version == 1
        rep = c.patch(f"/api/v1/resources/{r.id}/", {"nom": "Riz 2"}, format="json")
        assert rep.status_code == 200 and rep.data["version"] == 2 and rep["ETag"] == '"2"'
        assert c.get(f"/api/v1/resources/{r.id}/")["ETag"] == '"2"'

    def test_modification_avec_version_courante_acceptee(self, ctx):
        c, _, r = ctx
        rep = c.patch(f"/api/v1/resources/{r.id}/", {"nom": "A"}, format="json", HTTP_IF_MATCH='"1"')
        assert rep.status_code == 200

    def test_version_perimee_refusee_412_avec_donnees_serveur_et_rien_n_est_ecrase(self, ctx):
        c, _, r = ctx
        c.patch(f"/api/v1/resources/{r.id}/", {"nom": "Modif serveur"}, format="json")  # v2 (autre appareil)
        rep = c.patch(f"/api/v1/resources/{r.id}/", {"nom": "Modif hors ligne"}, format="json", HTTP_IF_MATCH='"1"')
        assert rep.status_code == 412
        assert rep.data["code"] == 412 and "modifiée" in rep.data["message"]
        assert rep.data["extra"]["version_serveur"] == 2
        assert rep.data["extra"]["donnees_serveur"]["nom"] == "Modif serveur"
        r.refresh_from_db(); assert r.nom == "Modif serveur" and r.version == 2

    def test_etag_faible_et_joker(self, ctx):
        c, _, r = ctx
        assert c.patch(f"/api/v1/resources/{r.id}/", {"nom": "A"}, format="json", HTTP_IF_MATCH='W/"1"').status_code == 200
        assert c.patch(f"/api/v1/resources/{r.id}/", {"nom": "B"}, format="json", HTTP_IF_MATCH="*").status_code == 200

    def test_sans_en_tete_comportement_historique_sauf_si_exige(self, ctx, settings):
        c, _, r = ctx
        assert c.patch(f"/api/v1/resources/{r.id}/", {"nom": "A"}, format="json").status_code == 200
        settings.EXIGER_PRECONDITION_MODIFICATION = True
        assert c.patch(f"/api/v1/resources/{r.id}/", {"nom": "B"}, format="json").status_code == 428

    def test_put_est_aussi_protege(self, ctx):
        c, secteur, r = ctx
        c.patch(f"/api/v1/resources/{r.id}/", {"nom": "v2"}, format="json")
        corps = {"type": "produit", "nom": "PUT", "secteur": secteur.id, "niveau_actuel": 1,
                 "seuil_critique": 1, "seuil_alerte": 2}
        assert c.put(f"/api/v1/resources/{r.id}/", corps, format="json", HTTP_IF_MATCH='"1"').status_code == 412

    def test_effet_de_bord_sur_le_stock_incremente_la_version_de_la_ressource(self, ctx):
        c, secteur, r = ctx
        c.post("/api/v1/transactions/", {"type": "mouvement_stock", "quantite": "-5", "ressource": r.id,
               "secteur": secteur.id, "date_transaction": timezone.now().isoformat()}, format="json")
        r.refresh_from_db(); assert r.version == 2
        # un client hors ligne qui a lu la ressource AVANT ce mouvement est détecté
        assert c.patch(f"/api/v1/resources/{r.id}/", {"nom": "X"}, format="json", HTTP_IF_MATCH='"1"').status_code == 412

    def test_isolation_sectorielle_prime_sur_la_precondition(self, ctx):
        c, _, _ = ctx
        autre = Secteur.objects.create(code="sante", nom="Santé")
        r2 = Ressource.objects.create(type="produit", nom="Autre", secteur=autre, niveau_actuel=1,
                                      seuil_critique=1, seuil_alerte=2)
        assert c.patch(f"/api/v1/resources/{r2.id}/", {"nom": "x"}, format="json", HTTP_IF_MATCH='"1"').status_code == 404


@pytest.mark.django_db
class TestIfUnmodifiedSince:
    def test_date_posterieure_a_la_derniere_modification_acceptee(self, ctx):
        c, _, r = ctx
        limite = format_datetime(timezone.now() + timedelta(seconds=5), usegmt=True)
        assert c.patch(f"/api/v1/resources/{r.id}/", {"nom": "A"}, format="json",
                       HTTP_IF_UNMODIFIED_SINCE=limite).status_code == 200

    def test_date_anterieure_a_la_derniere_modification_refusee_412(self, ctx):
        c, _, r = ctx
        limite = format_datetime(timezone.now() - timedelta(hours=1), usegmt=True)
        assert c.patch(f"/api/v1/resources/{r.id}/", {"nom": "A"}, format="json",
                       HTTP_IF_UNMODIFIED_SINCE=limite).status_code == 412

    def test_date_invalide_400_et_if_match_prioritaire(self, ctx):
        c, _, r = ctx
        assert c.patch(f"/api/v1/resources/{r.id}/", {"nom": "A"}, format="json",
                       HTTP_IF_UNMODIFIED_SINCE="pas une date").status_code == 400
        vieille = format_datetime(timezone.now() - timedelta(hours=1), usegmt=True)
        assert c.patch(f"/api/v1/resources/{r.id}/", {"nom": "B"}, format="json",
                       HTTP_IF_MATCH='"1"', HTTP_IF_UNMODIFIED_SINCE=vieille).status_code == 200


@pytest.mark.django_db
class TestAutresModeles:
    def test_entite_et_facture_versionnees(self, ctx):
        c, secteur, _ = ctx
        e = Entite.objects.create(type="client", nom="C", secteur=secteur)
        c.patch(f"/api/v1/entities/{e.id}/", {"nom": "C2"}, format="json")
        assert c.patch(f"/api/v1/entities/{e.id}/", {"nom": "C3"}, format="json", HTTP_IF_MATCH='"1"').status_code == 412
        f = Facture.objects.create(numero="F1", entite=e, montant="10.00", secteur=secteur)
        c.patch(f"/api/v1/invoices/{f.id}/", {"statut": "payee"}, format="json")
        assert c.patch(f"/api/v1/invoices/{f.id}/", {"statut": "impayee"}, format="json", HTTP_IF_MATCH='"1"').status_code == 412

"""Réouverture à DEUX personnes : demande (treasury.write) → approbation distincte (treasury.reopen) → brouillon."""
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.audit.models import JournalAudit
from apps.core.models import Secteur
from apps.resources.models import Ressource
from apps.treasury.models import DemandeReouverture, Transaction


def _role(nom, codes):
    r = Role.objects.create(nom=nom)
    for c in codes:
        p, _ = Permission.objects.get_or_create(code=c, defaults={"module": c.split(".")[0]})
        RolePermission.objects.get_or_create(role=r, permission=p)
    return r


def _client(role, secteur, nom):
    u = Utilisateur.objects.create_user(email=f"{nom}@x.com", nom_utilisateur=nom, password="MotDePasse#2026",
                                        role=role, secteur_principal=secteur)
    c = APIClient(); c.force_authenticate(user=u)
    return c, u


@pytest.fixture
def ctx(db):
    secteur = Secteur.objects.create(code="commerce", nom="Commerce")
    saisie, u_saisie = _client(_role("Comptable", ["treasury.read", "treasury.write"]), secteur, "compta")
    appro, u_appro = _client(_role("Responsable", ["treasury.read", "treasury.write", "treasury.reopen"]), secteur, "resp")
    t = Transaction.objects.create(type="entree", montant="500.00", secteur=secteur, date_transaction=timezone.now(), auteur=u_saisie)
    return saisie, u_saisie, appro, u_appro, secteur, t


@pytest.mark.django_db
class TestDoubleValidation:
    def test_reouverture_directe_interdite_par_defaut_meme_avec_la_permission(self, ctx):
        _, _, appro, _, _, t = ctx
        r = appro.post(f"/api/v1/transactions/{t.id}/reouvrir/", {"motif": "x"}, format="json")
        assert r.status_code == 409 and "double validation" in r.json()["detail"].lower()
        assert Transaction.objects.get(pk=t.id).statut == "validee"

    def test_parcours_complet_demande_puis_approbation_par_une_autre_personne(self, ctx):
        saisie, u_saisie, appro, u_appro, _, t = ctx
        d = saisie.post(f"/api/v1/transactions/{t.id}/demander-reouverture/", {"motif": "Mauvais type"}, format="json")
        assert d.status_code == 201 and d.json()["statut"] == "en_attente"
        assert Transaction.objects.get(pk=t.id).statut == "validee"            # RIEN ne change avant l'approbation
        r = appro.post(f"/api/v1/reopen-requests/{d.json()['id']}/approuver/", {"motif_decision": "OK"}, format="json")
        assert r.status_code == 200 and r.json()["statut"] == "approuvee"
        assert Transaction.objects.get(pk=t.id).statut == "brouillon"
        actions = set(JournalAudit.objects.values_list("action", flat=True))
        assert {"demande_reouverture", "decision_reouverture", "reouverture_transaction"} <= actions
        e = JournalAudit.objects.get(action="reouverture_transaction")
        assert e.auteur_id == u_appro.id and e.details["validation"] == "double" and e.details["demandeur_id"] == u_saisie.id

    def test_le_demandeur_ne_peut_jamais_approuver_sa_propre_demande(self, ctx):
        _, _, appro, _, _, t = ctx
        d = appro.post(f"/api/v1/transactions/{t.id}/demander-reouverture/", {"motif": "m"}, format="json").json()
        r = appro.post(f"/api/v1/reopen-requests/{d['id']}/approuver/", {}, format="json")
        assert r.status_code == 403 and Transaction.objects.get(pk=t.id).statut == "validee"

    def test_approuver_sans_la_permission_dediee_refuse(self, ctx):
        saisie, _, appro, _, secteur, t = ctx
        d = appro.post(f"/api/v1/transactions/{t.id}/demander-reouverture/", {"motif": "m"}, format="json").json()
        autre, _ = _client(_role("Comptable2", ["treasury.read", "treasury.write"]), secteur, "autre")
        assert autre.post(f"/api/v1/reopen-requests/{d['id']}/approuver/", {}, format="json").status_code == 403

    def test_refus_motive_obligatoire_et_la_transaction_reste_validee(self, ctx):
        saisie, _, appro, _, _, t = ctx
        d = saisie.post(f"/api/v1/transactions/{t.id}/demander-reouverture/", {"motif": "m"}, format="json").json()
        assert appro.post(f"/api/v1/reopen-requests/{d['id']}/refuser/", {}, format="json").status_code == 400
        r = appro.post(f"/api/v1/reopen-requests/{d['id']}/refuser/", {"motif_decision": "Pas justifié"}, format="json")
        assert r.status_code == 200 and r.json()["statut"] == "refusee"
        assert Transaction.objects.get(pk=t.id).statut == "validee"

    def test_une_demande_deja_decidee_ne_se_rejoue_pas(self, ctx):
        saisie, _, appro, _, _, t = ctx
        d = saisie.post(f"/api/v1/transactions/{t.id}/demander-reouverture/", {"motif": "m"}, format="json").json()
        appro.post(f"/api/v1/reopen-requests/{d['id']}/approuver/", {}, format="json")
        assert appro.post(f"/api/v1/reopen-requests/{d['id']}/approuver/", {}, format="json").status_code == 409

    def test_une_seule_demande_en_attente_par_transaction(self, ctx):
        saisie, _, _, _, _, t = ctx
        assert saisie.post(f"/api/v1/transactions/{t.id}/demander-reouverture/", {"motif": "a"}, format="json").status_code == 201
        assert saisie.post(f"/api/v1/transactions/{t.id}/demander-reouverture/", {"motif": "b"}, format="json").status_code == 409
        assert DemandeReouverture.objects.count() == 1

    def test_l_etat_est_revalide_a_l_approbation(self, ctx):
        """Entre la demande et l'approbation, la transaction est contre-passée : l'approbation doit échouer proprement."""
        from apps.treasury import workflow
        saisie, u_saisie, appro, _, _, t = ctx
        d = saisie.post(f"/api/v1/transactions/{t.id}/demander-reouverture/", {"motif": "m"}, format="json").json()
        workflow.contre_passer(t, u_saisie, "erreur")
        r = appro.post(f"/api/v1/reopen-requests/{d['id']}/approuver/", {}, format="json")
        assert r.status_code == 409
        assert DemandeReouverture.objects.get(pk=d["id"]).statut == "en_attente"      # rien n'a été validé à moitié

    def test_demande_impossible_sur_mouvement_de_stock(self, ctx):
        saisie, _, _, _, secteur, _ = ctx
        res = Ressource.objects.create(type="produit", nom="Riz", secteur=secteur, niveau_actuel=10, seuil_critique=1, seuil_alerte=2)
        mv = Transaction.objects.create(type="mouvement_stock", quantite=Decimal("1"), ressource=res, secteur=secteur,
                                        date_transaction=timezone.now())
        assert saisie.post(f"/api/v1/transactions/{mv.id}/demander-reouverture/", {"motif": "m"}, format="json").status_code == 409

    def test_approbation_rejouee_avec_la_meme_cle_ne_double_pas_l_effet(self, ctx):
        saisie, _, appro, _, _, t = ctx
        d = saisie.post(f"/api/v1/transactions/{t.id}/demander-reouverture/", {"motif": "m"}, format="json").json()
        for _ in range(2):
            r = appro.generic("POST", f"/api/v1/reopen-requests/{d['id']}/approuver/", "{}", content_type="application/json",
                              HTTP_IDEMPOTENCY_KEY="appr-1")
            assert r.status_code == 200
        assert JournalAudit.objects.filter(action="reouverture_transaction").count() == 1

    def test_mode_une_personne_reste_disponible_par_reglage(self, ctx, settings):
        _, _, appro, _, _, t = ctx
        settings.REOUVERTURE_DOUBLE_VALIDATION = False
        assert appro.post(f"/api/v1/transactions/{t.id}/reouvrir/", {"motif": "x"}, format="json").status_code == 200
        assert JournalAudit.objects.get(action="reouverture_transaction").details["validation"] == "directe"

    def test_isolation_sectorielle_des_demandes(self, ctx):
        saisie, _, appro, _, _, t = ctx
        d = saisie.post(f"/api/v1/transactions/{t.id}/demander-reouverture/", {"motif": "m"}, format="json").json()
        autre_secteur = Secteur.objects.create(code="sante", nom="Santé")
        etranger, _ = _client(_role("Resp2", ["treasury.read", "treasury.write", "treasury.reopen"]), autre_secteur, "etranger")
        assert etranger.get("/api/v1/reopen-requests/").json()["results"] == []
        assert etranger.post(f"/api/v1/reopen-requests/{d['id']}/approuver/", {}, format="json").status_code == 404

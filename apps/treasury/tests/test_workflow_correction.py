"""LOT A — immutabilité, contre-écriture, avoirs, réouverture encadrée, clôture, audit."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.audit.models import JournalAudit
from apps.core.models import Secteur
from apps.registry.models import Entite
from apps.resources.models import Ressource
from apps.treasury.models import Facture, Transaction


def _role(nom, codes):
    role = Role.objects.create(nom=nom)
    for code in codes:
        p, _ = Permission.objects.get_or_create(code=code, defaults={"module": code.split(".")[0]})
        RolePermission.objects.get_or_create(role=role, permission=p)
    return role


@pytest.fixture
def ctx(db):
    secteur = Secteur.objects.create(code="commerce", nom="Commerce")
    role = _role("Comptable", ["treasury.read", "treasury.write"])
    user = Utilisateur.objects.create_user(email="c@x.com", nom_utilisateur="compta", password="MotDePasse#2026",
                                           role=role, secteur_principal=secteur)
    client = APIClient(); client.force_authenticate(user=user)
    entite = Entite.objects.create(type="client", nom="Client", secteur=secteur)
    ressource = Ressource.objects.create(type="produit", nom="Riz", secteur=secteur,
                                         niveau_actuel=100, seuil_critique=5, seuil_alerte=10)
    return client, user, secteur, entite, ressource


def _tx(client, secteur, **kw):
    corps = {"type": "entree", "montant": "100000.00", "secteur": secteur.id,
             "date_transaction": timezone.now().isoformat(), **kw}
    return client.post("/api/v1/transactions/", corps, format="json")


@pytest.mark.django_db
class TestImmutabilite:
    def test_patch_financier_sur_transaction_validee_refuse_409(self, ctx):
        client, _, secteur, *_ = ctx
        t = _tx(client, secteur).data
        r = client.patch(f"/api/v1/transactions/{t['id']}/", {"montant": "1.00"}, format="json")
        assert r.status_code == 409
        assert Transaction.objects.get(pk=t["id"]).montant == Decimal("100000.00")

    def test_put_complet_avec_meme_valeurs_reste_possible_mais_pas_avec_valeur_differente(self, ctx):
        client, _, secteur, *_ = ctx
        t = _tx(client, secteur).data
        assert client.patch(f"/api/v1/transactions/{t['id']}/", {"montant": "100000.00"}, format="json").status_code == 200
        assert client.patch(f"/api/v1/transactions/{t['id']}/", {"description": "note"}, format="json").status_code == 200

    def test_statut_non_modifiable_par_patch(self, ctx):
        client, _, secteur, *_ = ctx
        t = _tx(client, secteur, statut="brouillon").data
        r = client.patch(f"/api/v1/transactions/{t['id']}/", {"statut": "validee"}, format="json")
        assert r.status_code == 400

    def test_garde_au_niveau_modele_meme_hors_api(self, ctx):
        _, _, secteur, *_ = ctx
        t = Transaction.objects.create(type="entree", montant="10.00", secteur=secteur, date_transaction=timezone.now())
        t.montant = Decimal("11.00")
        with pytest.raises(ValidationError):
            t.save()

    def test_suppression_validee_interdite_405_et_brouillon_supprimable(self, ctx):
        client, _, secteur, *_ = ctx
        valide = _tx(client, secteur).data
        assert client.delete(f"/api/v1/transactions/{valide['id']}/").status_code == 405
        brouillon = _tx(client, secteur, statut="brouillon").data
        assert client.delete(f"/api/v1/transactions/{brouillon['id']}/").status_code == 204
        assert Transaction.objects.filter(pk=valide["id"]).exists()

    def test_montant_negatif_refuse_a_la_creation(self, ctx):
        client, _, secteur, *_ = ctx
        assert _tx(client, secteur, montant="-5.00").status_code == 400


@pytest.mark.django_db
class TestBrouillon:
    def test_brouillon_modifiable_sans_effet_de_stock_ni_de_kpi(self, ctx):
        client, _, secteur, _, ressource = ctx
        t = client.post("/api/v1/transactions/", {
            "type": "mouvement_stock", "quantite": "-30", "ressource": ressource.id, "secteur": secteur.id,
            "date_transaction": timezone.now().isoformat(), "statut": "brouillon"}, format="json").data
        ressource.refresh_from_db(); assert ressource.niveau_actuel == 100  # aucun effet
        assert client.patch(f"/api/v1/transactions/{t['id']}/", {"quantite": "-20"}, format="json").status_code == 200
        r = client.post(f"/api/v1/transactions/{t['id']}/valider/")
        assert r.status_code == 200 and r.data["statut"] == "validee"
        ressource.refresh_from_db(); assert ressource.niveau_actuel == 80  # appliqué UNE fois, avec la valeur corrigée

    def test_brouillon_exclu_du_resume_tresorerie(self, ctx):
        client, _, secteur, *_ = ctx
        _tx(client, secteur, statut="brouillon")
        _tx(client, secteur, montant="500.00")
        r = client.get("/api/v1/transactions/summary/")
        assert Decimal(str(r.data["entrees_mois"])) == Decimal("500.00")

    def test_double_validation_refusee(self, ctx):
        client, _, secteur, *_ = ctx
        t = _tx(client, secteur, statut="brouillon").data
        assert client.post(f"/api/v1/transactions/{t['id']}/valider/").status_code == 200
        assert client.post(f"/api/v1/transactions/{t['id']}/valider/").status_code == 409


@pytest.mark.django_db
class TestContreEcriture:
    def test_contre_passation_conserve_l_original_et_annule_l_effet_financier(self, ctx):
        client, user, secteur, *_ = ctx
        t = _tx(client, secteur).data
        r = client.post(f"/api/v1/transactions/{t['id']}/contre-passer/", {"motif": "Erreur de saisie"}, format="json")
        assert r.status_code == 201
        assert Decimal(r.data["montant"]) == Decimal("-100000.00")
        assert r.data["contre_ecriture_de"] == t["id"]
        original = Transaction.objects.get(pk=t["id"])
        assert original.statut == "contrepassee" and original.montant == Decimal("100000.00")
        total = sum(x.montant for x in Transaction.objects.filter(type="entree"))
        assert total == 0  # net nul
        assert r.data["motif_correction"] == "Erreur de saisie"

    def test_contre_passation_d_un_mouvement_de_stock_retablit_le_stock(self, ctx):
        client, _, secteur, _, ressource = ctx
        t = client.post("/api/v1/transactions/", {
            "type": "mouvement_stock", "quantite": "-40", "ressource": ressource.id, "secteur": secteur.id,
            "date_transaction": timezone.now().isoformat()}, format="json").data
        ressource.refresh_from_db(); assert ressource.niveau_actuel == 60
        client.post(f"/api/v1/transactions/{t['id']}/contre-passer/", {"motif": "Erreur"}, format="json")
        ressource.refresh_from_db(); assert ressource.niveau_actuel == 100

    def test_double_contre_passation_et_contre_passation_d_une_contre_ecriture_refusees(self, ctx):
        client, _, secteur, *_ = ctx
        t = _tx(client, secteur).data
        inverse = client.post(f"/api/v1/transactions/{t['id']}/contre-passer/", {"motif": "m"}, format="json").data
        assert client.post(f"/api/v1/transactions/{t['id']}/contre-passer/", {"motif": "m"}, format="json").status_code == 409
        assert client.post(f"/api/v1/transactions/{inverse['id']}/contre-passer/", {"motif": "m"}, format="json").status_code == 409
        assert Transaction.objects.filter(type="entree").count() == 2

    def test_motif_obligatoire(self, ctx):
        client, _, secteur, *_ = ctx
        t = _tx(client, secteur).data
        assert client.post(f"/api/v1/transactions/{t['id']}/contre-passer/", {}, format="json").status_code == 400

    def test_corriger_cree_contre_ecriture_et_remplacement_atomiquement(self, ctx):
        client, _, secteur, *_ = ctx
        t = _tx(client, secteur).data
        r = client.post(f"/api/v1/transactions/{t['id']}/corriger/", {
            "motif": "Montant erroné",
            "remplacement": {"type": "entree", "montant": "120000.00", "secteur": secteur.id,
                             "date_transaction": timezone.now().isoformat()}}, format="json")
        assert r.status_code == 201
        assert r.data["remplacement"]["remplace"] == t["id"]
        net = sum(x.montant for x in Transaction.objects.filter(type="entree").exclude(statut="brouillon")
                  .exclude(statut="contrepassee").exclude(contre_ecriture_de__isnull=False))
        assert net == Decimal("120000.00")
        assert Transaction.objects.count() == 3

    def test_corriger_avec_remplacement_invalide_ne_modifie_rien(self, ctx):
        client, _, secteur, *_ = ctx
        t = _tx(client, secteur).data
        r = client.post(f"/api/v1/transactions/{t['id']}/corriger/", {
            "motif": "x", "remplacement": {"type": "entree", "secteur": secteur.id}}, format="json")
        assert r.status_code == 400
        assert Transaction.objects.count() == 1 and Transaction.objects.get().statut == "validee"

    def test_audit_complet_qui_quoi_quand_pourquoi_avant_apres(self, ctx):
        client, user, secteur, *_ = ctx
        t = _tx(client, secteur).data
        client.post(f"/api/v1/transactions/{t['id']}/contre-passer/", {"motif": "Doublon"}, format="json",
                    HTTP_X_REQUEST_ID="req-42")
        e = JournalAudit.objects.get(action="contre_passation_transaction")
        assert e.auteur_id == user.id and e.cible_id == str(t["id"])
        assert e.details["motif"] == "Doublon" and e.details["request_id"] == "req-42"
        assert e.details["ancien_etat"]["statut"] == "validee" and e.details["nouvel_etat"]["statut"] == "contrepassee"
        assert "contre_ecriture_id" in e.details


@pytest.mark.django_db
class TestReouverture:
    def test_sans_permission_refusee_403_et_rien_ne_change(self, ctx):
        client, _, secteur, *_ = ctx
        t = _tx(client, secteur).data
        r = client.post(f"/api/v1/transactions/{t['id']}/reouvrir/", {"motif": "x"}, format="json")
        assert r.status_code == 403
        assert Transaction.objects.get(pk=t["id"]).statut == "validee"

    def test_avec_permission_et_motif_passe_en_brouillon_avec_audit(self, ctx):
        client, _, secteur, *_ = ctx
        role = _role("Responsable", ["treasury.read", "treasury.write", "treasury.reopen"])
        resp = Utilisateur.objects.create_user(email="r@x.com", nom_utilisateur="resp", password="MotDePasse#2026",
                                               role=role, secteur_principal=secteur)
        c = APIClient(); c.force_authenticate(user=resp)
        t = _tx(client, secteur).data
        r = c.post(f"/api/v1/transactions/{t['id']}/reouvrir/", {"motif": "Erreur de type"}, format="json")
        assert r.status_code == 200 and r.data["statut"] == "brouillon"
        assert c.patch(f"/api/v1/transactions/{t['id']}/", {"montant": "90.00"}, format="json").status_code == 200
        assert c.post(f"/api/v1/transactions/{t['id']}/valider/").status_code == 200
        actions = list(JournalAudit.objects.values_list("action", flat=True))
        assert "reouverture_transaction" in actions and "validation_transaction" in actions

    def test_reouverture_sans_motif_refusee_et_mouvement_de_stock_non_rouvrable(self, ctx):
        _, _, secteur, _, ressource = ctx
        role = _role("Responsable", ["treasury.read", "treasury.write", "treasury.reopen"])
        resp = Utilisateur.objects.create_user(email="r@x.com", nom_utilisateur="resp", password="MotDePasse#2026",
                                               role=role, secteur_principal=secteur)
        c = APIClient(); c.force_authenticate(user=resp)
        t = c.post("/api/v1/transactions/", {"type": "mouvement_stock", "quantite": "-5", "ressource": ressource.id,
                   "secteur": secteur.id, "date_transaction": timezone.now().isoformat()}, format="json").data
        assert c.post(f"/api/v1/transactions/{t['id']}/reouvrir/", {}, format="json").status_code == 400
        assert c.post(f"/api/v1/transactions/{t['id']}/reouvrir/", {"motif": "x"}, format="json").status_code == 409


@pytest.mark.django_db
class TestPeriodeCloturee:
    def test_creation_datee_dans_une_periode_close_refusee(self, ctx):
        client, _, secteur, *_ = ctx
        secteur.date_cloture = timezone.now().date() - timedelta(days=10); secteur.save()
        ancienne = (timezone.now() - timedelta(days=20)).isoformat()
        assert _tx(client, secteur, date_transaction=ancienne).status_code == 400
        assert _tx(client, secteur).status_code == 201  # période ouverte

    def test_regularisation_d_une_transaction_de_periode_close_datee_du_jour(self, ctx):
        client, _, secteur, *_ = ctx
        ancienne = Transaction.objects.create(type="entree", montant="10.00", secteur=secteur,
                                              date_transaction=timezone.now() - timedelta(days=20))
        secteur.date_cloture = timezone.now().date() - timedelta(days=10); secteur.save()
        r = client.post(f"/api/v1/transactions/{ancienne.id}/contre-passer/", {"motif": "Régularisation"}, format="json")
        assert r.status_code == 201  # la contre-écriture est datée dans la période ouverte
        assert r.data["date_transaction"][:10] == timezone.now().date().isoformat()

    def test_modification_directe_impossible_reouverture_interdite_en_periode_close(self, ctx):
        _, _, secteur, *_ = ctx
        role = _role("Responsable", ["treasury.read", "treasury.write", "treasury.reopen"])
        resp = Utilisateur.objects.create_user(email="r@x.com", nom_utilisateur="resp", password="MotDePasse#2026",
                                               role=role, secteur_principal=secteur)
        c = APIClient(); c.force_authenticate(user=resp)
        t = Transaction.objects.create(type="entree", montant="10.00", secteur=secteur,
                                       date_transaction=timezone.now() - timedelta(days=20))
        secteur.date_cloture = timezone.now().date() - timedelta(days=10); secteur.save()
        assert c.post(f"/api/v1/transactions/{t.id}/reouvrir/", {"motif": "x"}, format="json").status_code == 400


@pytest.mark.django_db
class TestAvoirs:
    def _facture(self, ctx, numero="INV-001", montant="1000.00"):
        _, _, secteur, entite, _ = ctx
        return Facture.objects.create(numero=numero, entite=entite, montant=montant, taux_tva="18.00", secteur=secteur)

    def test_patch_financier_sur_facture_emise_refuse_409(self, ctx):
        client, *_ = ctx
        f = self._facture(ctx)
        assert client.patch(f"/api/v1/invoices/{f.id}/", {"montant": "1.00"}, format="json").status_code == 409
        assert client.patch(f"/api/v1/invoices/{f.id}/", {"statut": "annulee"}, format="json").status_code == 400

    def test_avoir_total_chaine_facture_avoir_et_original_annule(self, ctx):
        client, *_ = ctx
        f = self._facture(ctx)
        r = client.post(f"/api/v1/invoices/{f.id}/avoir/", {"motif": "Retour marchandise"}, format="json")
        assert r.status_code == 201
        assert r.data["numero"] == "AV-INV-001" and r.data["statut"] == "avoir" and r.data["avoir_de"] == f.id
        assert Decimal(r.data["montant"]) == Decimal("-1000.00") and Decimal(r.data["montant_tva"]) == Decimal("-180.00")
        f.refresh_from_db(); assert f.statut == "annulee" and f.montant_net == 0

    def test_avoirs_partiels_cumules_ne_depassent_jamais_le_montant(self, ctx):
        client, *_ = ctx
        f = self._facture(ctx)
        assert client.post(f"/api/v1/invoices/{f.id}/avoir/", {"motif": "m", "montant": "300.00"}, format="json").status_code == 201
        f.refresh_from_db(); assert f.statut == "emise" and f.montant_net == Decimal("700.00")
        r = client.post(f"/api/v1/invoices/{f.id}/avoir/", {"motif": "m", "montant": "800.00"}, format="json")
        assert r.status_code == 400
        r = client.post(f"/api/v1/invoices/{f.id}/avoir/", {"motif": "m", "montant": "700.00"}, format="json")
        assert r.status_code == 201 and r.data["numero"] == "AV-INV-001-2"
        f.refresh_from_db(); assert f.statut == "annulee"

    def test_avoir_sur_avoir_et_sur_facture_annulee_refuses(self, ctx):
        client, *_ = ctx
        f = self._facture(ctx)
        av = client.post(f"/api/v1/invoices/{f.id}/avoir/", {"motif": "m", "montant": "10"}, format="json").data
        assert client.post(f"/api/v1/invoices/{av['id']}/avoir/", {"motif": "m"}, format="json").status_code == 409
        client.post(f"/api/v1/invoices/{f.id}/annuler/", {"motif": "m"}, format="json")
        assert client.post(f"/api/v1/invoices/{f.id}/avoir/", {"motif": "m"}, format="json").status_code == 409

    def test_annuler_cree_un_avoir_total_et_audite(self, ctx):
        client, *_ = ctx
        f = self._facture(ctx)
        assert client.post(f"/api/v1/invoices/{f.id}/annuler/", {"motif": "Erreur"}, format="json").status_code == 200
        assert f.avoirs.count() == 1 and f.avoirs.get().montant == Decimal("-1000.00")
        assert JournalAudit.objects.filter(action="emission_avoir").exists()
        assert JournalAudit.objects.filter(action="annulation_facture").exists()

    def test_avoir_absent_des_alertes_et_montant_net_pour_facture_partiellement_creditee(self, ctx):
        from apps.alerts.detectors import FactureImpayeeDetecteur
        client, _, secteur, *_ = ctx
        f = self._facture(ctx)
        Facture.objects.filter(pk=f.pk).update(date_echeance=timezone.now().date() - timedelta(days=40), statut="impayee")
        client.post(f"/api/v1/invoices/{f.id}/avoir/", {"motif": "m", "montant": "400.00"}, format="json")
        (draft,) = FactureImpayeeDetecteur().detecter(secteur)
        assert "600.00" in draft.description  # net après avoir, pas 1000

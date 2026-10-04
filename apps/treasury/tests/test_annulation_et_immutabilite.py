"""
Pas de suppression physique des documents financiers (audit BE-008).
- Facture : DELETE refusé (405), annulation tracée via POST /annuler.
- Transaction : DELETE refusé (405).
"""
import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.audit.models import JournalAudit
from apps.core.models import Secteur
from apps.registry.models import Entite
from apps.treasury.models import Facture, Transaction


@pytest.fixture
def ctx(db):
    secteur = Secteur.objects.create(code="commerce", nom="Commerce")
    role = Role.objects.create(nom="Comptable")
    for code in ("treasury.read", "treasury.write"):
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"module": "treasury"})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    user = Utilisateur.objects.create_user(
        email="c@spipme.com", nom_utilisateur="compta", password="MotDePasse#2026",
        role=role, secteur_principal=secteur,
    )
    entite = Entite.objects.create(type="client", nom="Client", secteur=secteur)
    facture = Facture.objects.create(numero="F-1", entite=entite, montant="100.00", secteur=secteur)
    transaction = Transaction.objects.create(
        type="entree", montant="50.00", secteur=secteur, date_transaction=timezone.now(), auteur=user,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user, facture, transaction


@pytest.mark.django_db
class TestFacture:
    def test_delete_refuse_et_facture_conservee(self, ctx):
        client, _, facture, _ = ctx
        assert client.delete(f"/api/v1/invoices/{facture.id}/").status_code == 405
        assert Facture.objects.filter(pk=facture.pk).exists()

    def test_annulation_trace_motif_auteur_date_et_audit(self, ctx):
        client, user, facture, _ = ctx
        r = client.post(f"/api/v1/invoices/{facture.id}/annuler/", {"motif": "Erreur de saisie"}, format="json")
        assert r.status_code == 200 and r.data["statut"] == "annulee"
        facture.refresh_from_db()
        assert facture.motif_annulation == "Erreur de saisie"
        assert facture.annulee_par_id == user.id
        assert facture.date_annulation is not None
        assert JournalAudit.objects.filter(action="annulation_facture").exists()

    def test_annulation_sans_motif_refusee(self, ctx):
        client, _, facture, _ = ctx
        assert client.post(f"/api/v1/invoices/{facture.id}/annuler/", {}, format="json").status_code == 400
        assert client.post(f"/api/v1/invoices/{facture.id}/annuler/", {"motif": ""}, format="json").status_code == 400
        facture.refresh_from_db()
        assert facture.statut == "emise"

    def test_double_annulation_refusee_et_motif_original_preserve(self, ctx):
        client, _, facture, _ = ctx
        client.post(f"/api/v1/invoices/{facture.id}/annuler/", {"motif": "Premier"}, format="json")
        r = client.post(f"/api/v1/invoices/{facture.id}/annuler/", {"motif": "Second"}, format="json")
        assert r.status_code == 409
        facture.refresh_from_db()
        assert facture.motif_annulation == "Premier"

    def test_annulation_hors_perimetre_sectoriel_introuvable(self, ctx):
        client, _, _, _ = ctx
        autre = Secteur.objects.create(code="sante", nom="Santé")
        entite = Entite.objects.create(type="client", nom="X", secteur=autre)
        f = Facture.objects.create(numero="F-B", entite=entite, montant="10.00", secteur=autre)
        assert client.post(f"/api/v1/invoices/{f.id}/annuler/", {"motif": "x"}, format="json").status_code == 404

    def test_annulation_sans_permission_ecriture_refusee(self, ctx):
        _, _, facture, _ = ctx
        role = Role.objects.create(nom="Lecteur")
        perm = Permission.objects.get(code="treasury.read")
        RolePermission.objects.create(role=role, permission=perm)
        lecteur = Utilisateur.objects.create_user(
            email="l@spipme.com", nom_utilisateur="lect", password="MotDePasse#2026",
            role=role, secteur_principal=facture.secteur,
        )
        c = APIClient(); c.force_authenticate(user=lecteur)
        assert c.post(f"/api/v1/invoices/{facture.id}/annuler/", {"motif": "x"}, format="json").status_code == 403

    def test_annulation_et_creation_restent_possibles_en_lecture_patch(self, ctx):
        client, _, facture, _ = ctx
        assert client.patch(f"/api/v1/invoices/{facture.id}/", {"statut": "payee"}, format="json").status_code == 200


@pytest.mark.django_db
class TestTransaction:
    def test_delete_refuse_et_transaction_conservee(self, ctx):
        client, _, _, transaction = ctx
        assert client.delete(f"/api/v1/transactions/{transaction.id}/").status_code == 405
        assert Transaction.objects.filter(pk=transaction.pk).exists()

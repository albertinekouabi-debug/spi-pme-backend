"""Idempotence des créations — fondation du rejeu de la file offline (mobile)."""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import CleIdempotence, Secteur
from apps.resources.models import Ressource
from apps.treasury.models import Transaction


@pytest.fixture
def ctx(db):
    secteur = Secteur.objects.create(code="commerce", nom="Commerce")
    role = Role.objects.create(nom="Gérant")
    for module in ("treasury", "resources"):
        for action in ("read", "write"):
            p, _ = Permission.objects.get_or_create(code=f"{module}.{action}", defaults={"module": module})
            RolePermission.objects.get_or_create(role=role, permission=p)
    user = Utilisateur.objects.create_user(
        email="g@spipme.com", nom_utilisateur="g1", password="MotDePasse#2026", role=role, secteur_principal=secteur)
    c = APIClient(); c.force_authenticate(user=user)
    ressource = Ressource.objects.create(
        type="produit", nom="Riz", secteur=secteur, niveau_actuel=100, seuil_critique=5, seuil_alerte=10)
    return c, user, secteur, ressource


def _mouvement(secteur, ressource, quantite="-10"):
    return {"type": "mouvement_stock", "quantite": quantite, "secteur": secteur.id,
            "ressource": ressource.id, "date_transaction": timezone.now().isoformat()}


@pytest.mark.django_db
class TestIdempotence:
    def test_rejeu_ne_cree_pas_de_doublon_et_ne_double_pas_le_mouvement_de_stock(self, ctx):
        c, _, secteur, ressource = ctx
        payload = _mouvement(secteur, ressource)
        r1 = c.post("/api/v1/transactions/", payload, format="json", HTTP_IDEMPOTENCY_KEY="op-0001")
        r2 = c.post("/api/v1/transactions/", payload, format="json", HTTP_IDEMPOTENCY_KEY="op-0001")
        assert r1.status_code == 201 and r2.status_code == 201
        assert r2["Idempotent-Replayed"] == "true" and "Idempotent-Replayed" not in r1
        assert r1.data["id"] == r2.data["id"]
        assert Transaction.objects.count() == 1
        ressource.refresh_from_db()
        assert ressource.niveau_actuel == 90  # -10 appliqué UNE seule fois

    def test_sans_cle_deux_requetes_creent_deux_objets(self, ctx):
        c, _, secteur, ressource = ctx
        payload = _mouvement(secteur, ressource, "-1")
        c.post("/api/v1/transactions/", payload, format="json")
        c.post("/api/v1/transactions/", payload, format="json")
        assert Transaction.objects.count() == 2  # comportement historique inchangé

    def test_meme_cle_contenu_different_refuse_422(self, ctx):
        c, _, secteur, ressource = ctx
        c.post("/api/v1/transactions/", _mouvement(secteur, ressource, "-10"), format="json", HTTP_IDEMPOTENCY_KEY="k1")
        r = c.post("/api/v1/transactions/", _mouvement(secteur, ressource, "-99"), format="json", HTTP_IDEMPOTENCY_KEY="k1")
        assert r.status_code == 422
        assert Transaction.objects.count() == 1

    def test_erreur_de_validation_non_memorisee_donc_correction_possible_avec_la_meme_cle(self, ctx):
        c, _, secteur, ressource = ctx
        mauvais = {"type": "mouvement_stock", "secteur": secteur.id}  # quantité/ressource manquantes
        assert c.post("/api/v1/transactions/", mauvais, format="json", HTTP_IDEMPOTENCY_KEY="k2").status_code == 400
        assert CleIdempotence.objects.count() == 0
        ok = c.post("/api/v1/transactions/", _mouvement(secteur, ressource), format="json", HTTP_IDEMPOTENCY_KEY="k2")
        assert ok.status_code == 201

    def test_cle_invalide_refusee(self, ctx):
        c, _, secteur, ressource = ctx
        r = c.post("/api/v1/transactions/", _mouvement(secteur, ressource), format="json",
                   HTTP_IDEMPOTENCY_KEY="a b/c")
        assert r.status_code == 400

    def test_meme_cle_pour_deux_utilisateurs_distincts_sans_collision(self, ctx):
        c, _, secteur, ressource = ctx
        role = Role.objects.get(nom="Gérant")
        autre = Utilisateur.objects.create_user(
            email="h@spipme.com", nom_utilisateur="h1", password="MotDePasse#2026", role=role, secteur_principal=secteur)
        c2 = APIClient(); c2.force_authenticate(user=autre)
        payload = _mouvement(secteur, ressource, "-1")
        assert c.post("/api/v1/transactions/", payload, format="json", HTTP_IDEMPOTENCY_KEY="shared").status_code == 201
        r = c2.post("/api/v1/transactions/", payload, format="json", HTTP_IDEMPOTENCY_KEY="shared")
        assert r.status_code == 201 and "Idempotent-Replayed" not in r
        assert Transaction.objects.count() == 2

    def test_rejeu_apres_perte_de_reponse_sur_creation_de_ressource(self, ctx):
        c, _, secteur, _ = ctx
        payload = {"type": "produit", "nom": "Sucre", "secteur": secteur.id,
                   "niveau_actuel": 5, "seuil_critique": 1, "seuil_alerte": 2}
        for _ in range(3):
            assert c.post("/api/v1/resources/", payload, format="json", HTTP_IDEMPOTENCY_KEY="res-1").status_code == 201
        assert Ressource.objects.filter(nom="Sucre").count() == 1

    def test_l_isolation_sectorielle_reste_appliquee_avec_une_cle(self, ctx):
        c, _, _, ressource = ctx
        autre = Secteur.objects.create(code="sante", nom="Santé")
        r = c.post("/api/v1/transactions/", _mouvement(autre, ressource), format="json", HTTP_IDEMPOTENCY_KEY="x9")
        assert r.status_code in (400, 403)
        assert CleIdempotence.objects.count() == 0


@pytest.mark.django_db
def test_purge_supprime_uniquement_les_cles_expirees(ctx):
    from django.core.management import call_command
    _, user, _, _ = ctx
    vieille = CleIdempotence.objects.create(utilisateur=user, cle="old", methode="POST", chemin="/x",
                                            empreinte_corps="e", statut_http=201)
    CleIdempotence.objects.create(utilisateur=user, cle="new", methode="POST", chemin="/x",
                                  empreinte_corps="e", statut_http=201)
    CleIdempotence.objects.filter(pk=vieille.pk).update(date_creation=timezone.now() - timedelta(days=10))
    call_command("purger_cles_idempotence")
    assert list(CleIdempotence.objects.values_list("cle", flat=True)) == ["new"]


@pytest.mark.django_db
class TestIdempotenceDesActionsDeWorkflow:
    """Un rejeu d'action (réponse perdue) ne doit ni échouer en 409 ni produire un second effet."""

    def _facture(self, ctx):
        from apps.registry.models import Entite
        from apps.treasury.models import Facture
        _, _, secteur, _ = ctx
        e = Entite.objects.create(type="client", nom="C", secteur=secteur)
        return Facture.objects.create(numero="F-1", entite=e, montant="1000.00", secteur=secteur)

    def test_annuler_rejoue_renvoie_la_reponse_d_origine_sans_second_avoir(self, ctx):
        c, *_ = ctx
        f = self._facture(ctx)
        r1 = c.post(f"/api/v1/invoices/{f.id}/annuler/", {"motif": "Erreur"}, format="json", HTTP_IDEMPOTENCY_KEY="ann-1")
        r2 = c.post(f"/api/v1/invoices/{f.id}/annuler/", {"motif": "Erreur"}, format="json", HTTP_IDEMPOTENCY_KEY="ann-1")
        assert r1.status_code == 200 and r2.status_code == 200 and r2["Idempotent-Replayed"] == "true"
        assert f.avoirs.count() == 1
        # sans clé, le rejeu reste un 409 (état) : comportement historique préservé
        assert c.post(f"/api/v1/invoices/{f.id}/annuler/", {"motif": "Erreur"}, format="json").status_code == 409

    def test_contre_passation_rejouee_ne_cree_qu_une_seule_contre_ecriture(self, ctx):
        c, _, secteur, _ = ctx
        t = c.post("/api/v1/transactions/", {"type": "entree", "montant": "10.00", "secteur": secteur.id,
                   "date_transaction": timezone.now().isoformat()}, format="json").data
        for _ in range(3):
            r = c.post(f"/api/v1/transactions/{t['id']}/contre-passer/", {"motif": "m"}, format="json",
                       HTTP_IDEMPOTENCY_KEY="cp-1")
            assert r.status_code == 201
        assert Transaction.objects.count() == 2

    def test_meme_cle_sur_une_autre_action_refusee_422(self, ctx):
        c, *_ = ctx
        f = self._facture(ctx)
        c.post(f"/api/v1/invoices/{f.id}/avoir/", {"motif": "m", "montant": "10"}, format="json", HTTP_IDEMPOTENCY_KEY="k")
        r = c.post(f"/api/v1/invoices/{f.id}/annuler/", {"motif": "m"}, format="json", HTTP_IDEMPOTENCY_KEY="k")
        assert r.status_code == 422

"""
Contrat client↔serveur de la FILE HORS LIGNE Android : on rejoue exactement ce que la passerelle envoie
(chemins de OperationsMetier/dépôts, corps JSON tels que `jsonObjet` les produit : décimaux en chaînes,
`secteur` en entier, champs nuls omis, en-têtes Idempotency-Key / X-Request-ID / If-Match).
"""
import json

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.audit.models import JournalAudit
from apps.core.models import Secteur
from apps.registry.models import Entite
from apps.resources.models import Ressource
from apps.tasks.models import Tache
from apps.treasury.models import Facture, Transaction


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
    return c, secteur, user


def envoyer(c, methode, chemin, corps, cle, **extra):
    """Imite PasserelleOkHttp : corps JSON brut + en-têtes."""
    return c.generic(methode, f"/api/v1/{chemin}", json.dumps(corps), content_type="application/json",
                     HTTP_IDEMPOTENCY_KEY=cle, HTTP_X_REQUEST_ID=cle, **extra)


def charges(secteur):
    """Corps identiques à ceux de Ressource/Entite/Tache/TransactionRepositoryImpl.creer()."""
    maintenant = timezone.now().isoformat()
    return [
        ("resources/", {"type": "produit", "nom": "Riz", "unite": "kg", "niveau_actuel": "50", "seuil_critique": "5",
                        "seuil_alerte": "10", "emplacement": "Dépôt A", "secteur": secteur.id}, Ressource),
        ("entities/", {"type": "client", "nom": "Client A", "telephone": "+242060000000", "ville": "Pointe-Noire",
                       "secteur": secteur.id}, Entite),
        ("tasks/", {"titre": "Relancer", "priorite": "moyenne", "secteur": secteur.id}, Tache),
        ("transactions/", {"type": "entree", "montant": "15000.00", "mode_paiement": "especes",
                           "description": "Vente", "secteur": secteur.id, "date_transaction": maintenant}, Transaction),
    ]


@pytest.mark.django_db
class TestCreationsRejouees:
    def test_chaque_creation_est_acceptee_et_un_rejeu_ne_cree_pas_de_doublon(self, ctx):
        c, secteur, _ = ctx
        for chemin, corps, modele in charges(secteur):
            cle = f"op-{chemin.strip('/')}"
            r1 = envoyer(c, "POST", chemin, corps, cle)
            assert r1.status_code == 201, (chemin, r1.content)
            assert "id" in r1.json()                      # la passerelle lit `id` pour résoudre @local:
            r2 = envoyer(c, "POST", chemin, corps, cle)   # rejeu après réponse perdue
            assert r2.status_code == 201 and r2["Idempotent-Replayed"] == "true"
            assert r2.json()["id"] == r1.json()["id"]
            assert modele.objects.count() == 1, chemin

    def test_reference_locale_resolue_puis_mouvement_de_stock_sur_la_ressource_creee_hors_ligne(self, ctx):
        c, secteur, _ = ctx
        res = envoyer(c, "POST", "resources/", charges(secteur)[0][1], "k-res").json()
        # le moteur remplace "@local:<uuid>" par l'id serveur avant l'envoi suivant
        mouvement = {"type": "mouvement_stock", "quantite": "-5", "ressource": res["id"], "secteur": secteur.id,
                     "date_transaction": timezone.now().isoformat()}
        assert envoyer(c, "POST", "transactions/", mouvement, "k-mvt").status_code == 201
        assert Ressource.objects.get(pk=res["id"]).niveau_actuel == 45


@pytest.mark.django_db
class TestActionsRejouees:
    def test_annuler_facture_et_contre_passer_avec_les_chemins_exacts_de_la_file(self, ctx):
        c, secteur, user = ctx
        e = Entite.objects.create(type="client", nom="C", secteur=secteur)
        f = Facture.objects.create(numero="F1", entite=e, montant="1000.00", secteur=secteur)
        r = envoyer(c, "POST", f"invoices/{f.id}/annuler/", {"motif": "Erreur \"client\"\nligne 2"}, "k-ann")
        assert r.status_code == 200 and Facture.objects.get(pk=f.id).motif_annulation.startswith('Erreur "client"')
        # le X-Request-ID de la file relie l'audit serveur à l'opération locale
        assert JournalAudit.objects.filter(action="emission_avoir", details__request_id="k-ann").exists()

        t = Transaction.objects.create(type="entree", montant="10.00", secteur=secteur, date_transaction=timezone.now())
        assert envoyer(c, "POST", f"transactions/{t.id}/contre-passer/", {"motif": "Doublon"}, "k-cp").status_code == 201
        assert JournalAudit.objects.filter(action="contre_passation_transaction", details__request_id="k-cp").exists()


@pytest.mark.django_db
class TestModificationRejouee:
    def _ressource(self, secteur):
        return Ressource.objects.create(type="produit", nom="Riz", secteur=secteur, niveau_actuel=50, seuil_critique=5, seuil_alerte=10)

    def test_patch_avec_if_match_de_la_file(self, ctx):
        c, secteur, _ = ctx
        r = self._ressource(secteur)
        rep = envoyer(c, "PATCH", f"resources/{r.id}/", {"nom": "Riz long", "seuil_critique": "3", "seuil_alerte": "8"}, "k-p1", HTTP_IF_MATCH='"1"')
        assert rep.status_code == 200 and rep.json()["version"] == 2

    def test_patch_rejoue_apres_reponse_perdue_ne_doit_pas_etre_pris_pour_un_conflit(self, ctx):
        """Le 1er envoi a abouti (v1→v2) mais la réponse s'est perdue ; la file rejoue avec If-Match "1"."""
        c, secteur, _ = ctx
        r = self._ressource(secteur)
        corps = {"nom": "Riz long"}
        assert envoyer(c, "PATCH", f"resources/{r.id}/", corps, "k-p2", HTTP_IF_MATCH='"1"').status_code == 200
        rejeu = envoyer(c, "PATCH", f"resources/{r.id}/", corps, "k-p2", HTTP_IF_MATCH='"1"')
        assert rejeu.status_code == 200, "faux conflit : le serveur a rejeté le rejeu de SA PROPRE modification"
        assert rejeu["Idempotent-Replayed"] == "true"
        r.refresh_from_db(); assert r.version == 2 and r.nom == "Riz long"

    def test_un_vrai_conflit_reste_un_conflit_meme_avec_une_cle_neuve(self, ctx):
        c, secteur, _ = ctx
        r = self._ressource(secteur)
        envoyer(c, "PATCH", f"resources/{r.id}/", {"nom": "Autre appareil"}, "k-a", HTTP_IF_MATCH='"1"')
        rep = envoyer(c, "PATCH", f"resources/{r.id}/", {"nom": "Moi, hors ligne"}, "k-b", HTTP_IF_MATCH='"1"')
        assert rep.status_code == 412 and rep.json()["extra"]["version_serveur"] == 2


@pytest.mark.django_db
class TestResolutionDeConflitAvecLaMemeCle:
    """« Garder ma version » re-met en file la MÊME opération (même clé) avec la version serveur à jour."""

    def test_un_412_n_est_pas_memorise_et_la_reprise_avec_la_bonne_version_aboutit(self, ctx):
        c, secteur, _ = ctx
        r = Ressource.objects.create(type="produit", nom="Riz", secteur=secteur, niveau_actuel=50, seuil_critique=5, seuil_alerte=10)
        envoyer(c, "PATCH", f"resources/{r.id}/", {"nom": "Autre appareil"}, "k-autre", HTTP_IF_MATCH='"1"')   # v2
        corps = {"nom": "Ma version"}
        assert envoyer(c, "PATCH", f"resources/{r.id}/", corps, "k-moi", HTTP_IF_MATCH='"1"').status_code == 412
        reprise = envoyer(c, "PATCH", f"resources/{r.id}/", corps, "k-moi", HTTP_IF_MATCH='"2"')   # même clé, version à jour
        assert reprise.status_code == 200, "le 412 mémorisé empêche la résolution du conflit"
        r.refresh_from_db(); assert r.nom == "Ma version" and r.version == 3

    def test_un_refus_metier_409_n_est_pas_memorise(self, ctx):
        c, secteur, user = ctx
        from apps.treasury import workflow
        t = Transaction.objects.create(type="entree", montant="10.00", secteur=secteur, date_transaction=timezone.now())
        secteur.date_cloture = timezone.now().date() + timezone.timedelta(days=1); secteur.save()
        assert envoyer(c, "POST", f"transactions/{t.id}/contre-passer/", {"motif": "m"}, "k-cl").status_code == 400  # période close
        secteur.date_cloture = None; secteur.save()
        assert envoyer(c, "POST", f"transactions/{t.id}/contre-passer/", {"motif": "m"}, "k-cl").status_code == 201   # même clé, situation corrigée


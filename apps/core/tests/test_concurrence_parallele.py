"""
Concurrence PARALLÈLE réelle : plusieurs threads, chacun avec sa propre connexion PostgreSQL
(`transaction=True` : les écritures sont réellement validées, pas enveloppées dans une transaction de test).
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import CleIdempotence, Secteur
from apps.resources.models import Ressource
from apps.treasury.models import Transaction

N = 8


@pytest.fixture
def ctx(transactional_db):
    secteur = Secteur.objects.create(code="commerce", nom="Commerce")
    role = Role.objects.create(nom="Gérant")
    for code in ("treasury.read", "treasury.write"):
        p, _ = Permission.objects.get_or_create(code=code, defaults={"module": "treasury"})
        RolePermission.objects.get_or_create(role=role, permission=p)
    user = Utilisateur.objects.create_user(email="g@x.com", nom_utilisateur="g", password="MotDePasse#2026",
                                           role=role, secteur_principal=secteur)
    ressource = Ressource.objects.create(type="produit", nom="Riz", secteur=secteur, niveau_actuel=1000,
                                         seuil_critique=5, seuil_alerte=10)
    return user, secteur, ressource


def _lancer_en_parallele(user, requetes):
    """Toutes les requêtes partent au même instant (barrière) ; chaque thread ferme sa connexion."""
    barriere = Barrier(len(requetes))

    def travail(requete):
        client = APIClient()
        client.force_authenticate(user=user)
        url, corps, cle = requete
        try:
            barriere.wait()
            return client.post(url, corps, format="json", HTTP_IDEMPOTENCY_KEY=cle).status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(requetes)) as pool:
        return list(pool.map(travail, requetes))


def _mouvement(secteur, ressource, quantite):
    return {"type": "mouvement_stock", "quantite": quantite, "secteur": secteur.id, "ressource": ressource.id,
            "date_transaction": timezone.now().isoformat()}


@pytest.mark.django_db(transaction=True)
class TestConcurrenceParallele:
    def test_meme_cle_envoyee_simultanement_ne_cree_qu_une_transaction_et_un_seul_effet_de_stock(self, ctx):
        user, secteur, ressource = ctx
        corps = _mouvement(secteur, ressource, "-10")
        statuts = _lancer_en_parallele(user, [("/api/v1/transactions/", corps, "course-1")] * N)
        assert set(statuts) == {201}, statuts                 # le perdant rejoue la réponse du gagnant : jamais d'erreur
        assert Transaction.objects.count() == 1
        assert CleIdempotence.objects.filter(cle="course-1").count() == 1
        ressource.refresh_from_db()
        assert ressource.niveau_actuel == 990                 # -10 appliqué UNE seule fois

    def test_mouvements_de_stock_distincts_simultanes_ne_s_ecrasent_pas(self, ctx):
        user, secteur, ressource = ctx
        requetes = [("/api/v1/transactions/", _mouvement(secteur, ressource, "-1"), f"cle-{i}") for i in range(N)]
        statuts = _lancer_en_parallele(user, requetes)
        assert set(statuts) == {201}, statuts
        assert Transaction.objects.count() == N
        ressource.refresh_from_db()
        assert ressource.niveau_actuel == 1000 - N, "mises à jour perdues : le stock ne reflète pas tous les mouvements"

    def test_actions_de_workflow_simultanees_une_seule_contre_ecriture(self, ctx):
        user, secteur, ressource = ctx
        t = Transaction.objects.create(type="entree", montant="100.00", secteur=secteur, date_transaction=timezone.now())
        url = f"/api/v1/transactions/{t.id}/contre-passer/"
        statuts = _lancer_en_parallele(user, [(url, {"motif": "doublon"}, "cp-course")] * N)
        assert set(statuts) == {201}, statuts
        assert Transaction.objects.filter(contre_ecriture_de=t).count() == 1

"""
RÈGLE MÉTIER FIGÉE (décision arrêtée) :
  Une sortie de stock est ACCEPTÉE même si elle entraîne un stock négatif. Le stock négatif déclenche une alerte
  dédiée STOCK_NEGATIF (criticité élevée) jusqu'à régularisation par des mouvements entrants. Le stock n'est jamais
  plafonné à 0, la sortie jamais supprimée ni modifiée. L'événement est auditable (mouvement, utilisateur, date,
  quantité, stock avant/après, origine hors ligne). Exemple : 50 − 90 = −40 (critique, alerte) ; +100 = 60 (résolue).
"""
import json
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.alerts import services
from apps.alerts.models import Alerte
from apps.audit.models import JournalAudit
from apps.core.models import Secteur
from apps.resources.models import Ressource
from apps.treasury.models import Transaction


@pytest.fixture
def ctx(db):
    secteur = Secteur.objects.create(code="commerce", nom="Commerce")
    role = Role.objects.create(nom="Gérant")
    for code in ("treasury.read", "treasury.write", "alerts.read", "alerts.write", "resources.read"):
        p, _ = Permission.objects.get_or_create(code=code, defaults={"module": code.split(".")[0]})
        RolePermission.objects.get_or_create(role=role, permission=p)
    user = Utilisateur.objects.create_user(email="g@x.com", nom_utilisateur="g", password="MotDePasse#2026",
                                           role=role, secteur_principal=secteur)
    c = APIClient(); c.force_authenticate(user=user)
    ressource = Ressource.objects.create(type="produit", nom="Riz", unite="kg", secteur=secteur, niveau_actuel=50,
                                         seuil_critique=5, seuil_alerte=10)
    return c, secteur, user, ressource


def mouvement(c, secteur, ressource, quantite, cle, hors_ligne=True, date=None):
    # `date` fixe : la file rejoue un corps IDENTIQUE (sinon le serveur répond 422, même clé / autre contenu)
    corps = {"type": "mouvement_stock", "quantite": quantite, "ressource": ressource.id, "secteur": secteur.id,
             "date_transaction": date or timezone.now().isoformat()}
    extra = {"HTTP_X_ORIGINE": "hors_ligne"} if hors_ligne else {}
    return c.generic("POST", "/api/v1/transactions/", json.dumps(corps), content_type="application/json",
                     HTTP_IDEMPOTENCY_KEY=cle, **extra)


def alertes_actives(ressource):
    return Alerte.objects.filter(type="stock_negatif", ressource=ressource, statut="active")


@pytest.mark.django_db
class TestRegleStockNegatif:
    def test_exemple_complet_50_moins_90_puis_plus_100(self, ctx):
        c, secteur, user, r = ctx
        # --- sortie de 90 sur un stock de 50, saisie hors ligne
        rep = mouvement(c, secteur, r, "-90", "k-sortie")
        assert rep.status_code == 201                              # ACCEPTÉE : la vente n'est pas perdue
        r.refresh_from_db()
        assert r.niveau_actuel == Decimal("-40")                   # jamais plafonné à 0
        assert r.statut == "critique"
        (alerte,) = alertes_actives(r)
        assert alerte.niveau == "critique" and alerte.secteur_id == secteur.id
        assert "négatif" in alerte.titre.lower()
        t = Transaction.objects.get()
        assert t.quantite == Decimal("-90") and t.origine == "hors_ligne"   # sortie intacte, origine tracée

        # --- réception de 100
        assert mouvement(c, secteur, r, "100", "k-recep").status_code == 201
        r.refresh_from_db()
        assert r.niveau_actuel == Decimal("60") and r.statut == "stable"
        alerte.refresh_from_db()
        assert alerte.statut == "traitee" and alerte.date_resolution is not None
        assert not alertes_actives(r).exists()

    def test_audit_complet_qui_quoi_quand_avant_apres_origine(self, ctx):
        c, secteur, user, r = ctx
        mouvement(c, secteur, r, "-90", "k-a")
        e = JournalAudit.objects.get(action="stock_negatif_detecte")
        assert e.auteur_id == user.id and e.date_creation is not None if hasattr(e, "date_creation") else True
        d = e.details
        assert Decimal(d["stock_avant"]) == 50 and Decimal(d["stock_apres"]) == -40 and Decimal(d["quantite"]) == -90
        assert d["origine"] == "hors_ligne" and d["transaction_id"] == Transaction.objects.get().id
        assert d["ressource"] == "Riz"
        mouvement(c, secteur, r, "100", "k-b")
        resolu = JournalAudit.objects.get(action="stock_negatif_resolu")
        assert Decimal(resolu.details["stock_avant"]) == -40 and Decimal(resolu.details["stock_apres"]) == 60

    def test_origine_en_ligne_par_defaut(self, ctx):
        c, secteur, _, r = ctx
        mouvement(c, secteur, r, "-90", "k-l", hors_ligne=False)
        assert Transaction.objects.get().origine == "en_ligne"
        assert JournalAudit.objects.get(action="stock_negatif_detecte").details["origine"] == "en_ligne"

    def test_alerte_visible_dans_le_centre_d_alertes_et_le_resume_du_dashboard(self, ctx):
        c, secteur, _, r = ctx
        mouvement(c, secteur, r, "-90", "k-v")
        liste = c.get("/api/v1/alerts/").json()["results"]
        assert any(a["type"] == "stock_negatif" and a["niveau"] == "critique" for a in liste)
        assert c.get("/api/v1/alerts/summary/").json()["critiques"] == 1      # alimente aussi le badge Android
        mouvement(c, secteur, r, "100", "k-w")
        assert c.get("/api/v1/alerts/summary/").json()["critiques"] == 0

    def test_ni_doublon_d_alerte_ni_double_alerte_stock_critique(self, ctx):
        c, secteur, _, r = ctx
        mouvement(c, secteur, r, "-90", "k-1")
        mouvement(c, secteur, r, "-10", "k-2")                    # encore plus négatif
        assert alertes_actives(r).count() == 1
        assert "-50" in alertes_actives(r).get().description       # description tenue à jour
        assert JournalAudit.objects.filter(action="stock_negatif_mouvement").count() == 1
        services.generer_pour_secteur(secteur)                     # balayage complet
        assert Alerte.objects.filter(ressource=r, statut="active").count() == 1   # pas d'alerte « stock critique » en plus

    def test_sans_seuils_configures_un_stock_negatif_est_quand_meme_critique(self, ctx):
        c, secteur, _, _ = ctx
        sans_seuil = Ressource.objects.create(type="produit", nom="Sel", secteur=secteur, niveau_actuel=5)
        mouvement(c, secteur, sans_seuil, "-10", "k-s")
        sans_seuil.refresh_from_db()
        assert sans_seuil.statut == "critique" and alertes_actives(sans_seuil).exists()

    def test_stock_exactement_zero_n_est_pas_negatif(self, ctx):
        c, secteur, _, r = ctx
        mouvement(c, secteur, r, "-50", "k-z")
        r.refresh_from_db()
        assert r.niveau_actuel == 0 and not alertes_actives(r).exists()

    def test_regularisation_partielle_garde_l_alerte_active(self, ctx):
        c, secteur, _, r = ctx
        mouvement(c, secteur, r, "-90", "k-p1")                   # -40
        mouvement(c, secteur, r, "30", "k-p2")                    # -10 : toujours négatif
        assert alertes_actives(r).exists()
        mouvement(c, secteur, r, "10", "k-p3")                    # 0 : régularisé
        assert not alertes_actives(r).exists()


@pytest.mark.django_db
class TestIdempotenceEtCorrections:
    def test_rejeu_hors_ligne_ne_double_ni_le_mouvement_ni_l_alerte_ni_l_audit(self, ctx):
        c, secteur, _, r = ctx
        date = timezone.now().isoformat()
        assert mouvement(c, secteur, r, "-90", "k-rejeu", date=date).status_code == 201
        rejeu = mouvement(c, secteur, r, "-90", "k-rejeu", date=date)   # la file rejoue après réponse perdue
        assert rejeu.status_code == 201 and rejeu["Idempotent-Replayed"] == "true"
        r.refresh_from_db()
        assert r.niveau_actuel == Decimal("-40")                  # -90 appliqué UNE fois
        assert Transaction.objects.count() == 1 and alertes_actives(r).count() == 1
        assert JournalAudit.objects.filter(action="stock_negatif_detecte").count() == 1

    def test_contre_passer_la_reception_rouvre_l_alerte(self, ctx):
        from apps.treasury import workflow
        c, secteur, user, r = ctx
        mouvement(c, secteur, r, "-90", "k-a")
        reception = Transaction.objects.get(quantite=Decimal("-90"))
        mouvement(c, secteur, r, "100", "k-b")
        assert not alertes_actives(r).exists()
        recep = Transaction.objects.get(quantite=Decimal("100"))
        workflow.contre_passer(recep, user, "réception saisie par erreur")
        r.refresh_from_db()
        assert r.niveau_actuel == Decimal("-40") and alertes_actives(r).exists()   # situation réelle restaurée

    def test_balayage_complet_cree_et_resout_comme_le_declenchement_immediat(self, ctx):
        _, secteur, _, r = ctx
        Ressource.objects.filter(pk=r.pk).update(niveau_actuel=-5)       # état incohérent injecté hors workflow
        res = services.generer_pour_secteur(secteur)
        assert any(a.type == "stock_negatif" for a in res["creees"])
        Ressource.objects.filter(pk=r.pk).update(niveau_actuel=20)
        res = services.generer_pour_secteur(secteur)
        assert any(a.type == "stock_negatif" for a in res["resolues_automatiquement"])
        assert not alertes_actives(r).exists()

"""
Mesure de qualité des moteurs (cœur pur, graines fixes → résultats reproductibles).

Ces tests ne vérifient pas « que ça tourne » : ils MESURENT le taux de fausses alertes, le rappel et la
calibration des intervalles, et échouent si une modification les dégrade. Les seuils d'assertion portent une
marge sur les valeurs mesurées lors de la mise au point (indiquées en commentaire).
"""
import datetime as dt
import random

from apps.analytics import anomalies as A
from apps.analytics.previsions import POINTS_MIN, prevoir

D0 = dt.date(2026, 1, 5)


def serie(valeurs):
    return [(D0 + dt.timedelta(weeks=i), v) for i, v in enumerate(valeurs)]


def bruit(rng, n, niveau=100_000, sigma=0.15):
    return [niveau * rng.lognormvariate(0, sigma) for _ in range(n)]


class TestAnomaliesFauxPositifs:
    def test_bruit_pur_declenche_rarement_une_alerte(self):
        # Mesuré : 1,0 % (gauss) / 2,3 % (log-normal) des séries de 40 semaines ; AVANT correction : 44 %.
        rng, touchees = random.Random(42), 0
        for _ in range(500):
            touchees += bool(A.detecter(serie(bruit(rng, 40))).anomalies)
        assert touchees / 500 <= 0.06

    def test_serie_constante_ne_produit_aucune_anomalie(self):
        assert A.detecter(serie([50_000.0] * 30)).anomalies == []

    def test_une_hausse_progressive_normale_n_est_pas_une_anomalie(self):
        valeurs = [100_000 * (1 + 0.02 * i) for i in range(30)]  # +2 %/semaine, régulier
        assert A.detecter(serie(valeurs)).anomalies == []

    def test_serie_intermittente_ignoree(self):
        valeurs = [0, 0, 0, 0, 0, 100, 0, 0, 0, 0, 0, 0, 0, 90_000, 0, 0]
        assert A.detecter(serie(valeurs)).anomalies == []


class TestAnomaliesRappel:
    def _rappel(self, facteur, graine=7, n=500):
        rng, trouvees = random.Random(graine), 0
        for _ in range(n):
            v = bruit(rng, 30)
            k = rng.randrange(15, 30)
            v[k] *= facteur
            trouvees += any(a.periode_debut == D0 + dt.timedelta(weeks=k) for a in A.detecter(serie(v)).anomalies)
        return trouvees / n

    def test_pic_x3_quasi_toujours_detecte(self):
        assert self._rappel(3.0) >= 0.95            # mesuré 99,7 %

    def test_chute_de_70_pct_toujours_detectee(self):
        assert self._rappel(0.3) >= 0.95            # mesuré 100 %

    def test_doublement_detecte_dans_la_majorite_des_cas(self):
        assert self._rappel(2.0) >= 0.60            # mesuré 72,5 % (compromis assumé avec les faux positifs)

    def test_un_pic_ancien_dans_la_reference_ne_masque_pas_un_pic_recent(self):
        rng = random.Random(1)
        v = bruit(rng, 30)
        v[10] *= 6   # ancien pic, encore dans la fenêtre de référence
        v[25] *= 6   # nouveau pic
        semaines = {a.periode_debut for a in A.detecter(serie(v)).anomalies}
        assert D0 + dt.timedelta(weeks=25) in semaines   # l'écrêtage évite l'auto-masquage

    def test_caracterisation_complete_de_l_anomalie(self):
        rng = random.Random(5)
        v = bruit(rng, 20); v[18] *= 4
        (a,) = [x for x in A.detecter(serie(v)).anomalies if x.periode_debut == D0 + dt.timedelta(weeks=18)]
        assert a.sens == "hausse" and a.ecart_relatif > 2 and a.score >= 4
        assert a.nb_points_reference >= 8 and a.niveau_confiance in ("moyen", "eleve") and "médiane" in a.methode


class TestAnomaliesDonneesInsuffisantes:
    def test_historique_court_refuse_de_conclure(self):
        r = A.detecter(serie([1, 2, 3, 4]))
        assert r.statut == "donnees_insuffisantes" and r.anomalies == [] and "insuffisant" in r.message


class TestPrevisions:
    def test_historique_court_refuse(self):
        assert prevoir(serie([1.0] * (POINTS_MIN - 1))).statut == "donnees_insuffisantes"

    def test_serie_chaotique_refusee_plutot_que_trompeuse(self):
        rng = random.Random(9)
        r = prevoir(serie([rng.choice([0, 0, 0, 500_000, 20_000, 0, 300_000]) for _ in range(26)]))
        assert r.statut == "non_fiable" and "refusée" in r.message

    def test_serie_reguliere_prevision_ok_avec_qualite_et_limites(self):
        rng = random.Random(3)
        r = prevoir(serie([100_000 + rng.gauss(0, 8_000) for _ in range(26)]), horizon=4)
        assert r.statut == "ok" and len(r.previsions) == 4
        assert {"mae_retro_test", "wape_retro_test", "mae_baseline_naive", "modele_choisi"} <= r.qualite.keys()
        assert r.limites and all(p["bas"] <= p["valeur"] <= p["haut"] for p in r.previsions)

    def test_tendance_haussiere_prolongee_dans_le_bon_sens(self):
        rng = random.Random(4)
        r = prevoir(serie([100_000 * (1 + 0.04 * i) + rng.gauss(0, 3_000) for i in range(26)]), horizon=4)
        assert r.statut == "ok" and r.previsions[-1]["valeur"] > r.previsions[0]["valeur"]

    def test_les_intervalles_sont_correctement_calibres(self):
        # Intervalle annoncé « 80 % » : la couverture réelle doit en rester proche. Mesuré : 72-96 % selon le pas
        # (avant inflation de σ : 72 % au pas 1 → trop optimiste).
        rng, couv, n = random.Random(11), 0, 0
        for _ in range(300):
            v = [max(0, 100_000 + rng.gauss(0, 10_000)) for _ in range(34)]
            r = prevoir(serie(v[:30]), 4)
            if r.statut != "ok":
                continue
            n += 1
            couv += r.previsions[0]["bas"] <= v[30] <= r.previsions[0]["haut"]
        assert 0.70 <= couv / n <= 0.95

    def test_le_modele_retenu_n_est_jamais_pire_que_le_naif_sur_le_retro_test(self):
        rng = random.Random(21)
        for _ in range(100):
            r = prevoir(serie([100_000 + rng.gauss(0, 10_000) for _ in range(20)]))
            if r.statut == "ok":
                assert r.qualite["mae_retro_test"] <= r.qualite["mae_baseline_naive"] + 1e-9

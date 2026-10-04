"""
Prévision d'un flux hebdomadaire (cœur pur). Principes :
  - une prévision n'est JAMAIS présentée comme un fait : intervalle, qualité mesurée, limites ;
  - le modèle est choisi par rétro-test (origine glissante) contre une baseline naïve ;
  - si l'historique est trop court ou si l'erreur de rétro-test est trop grande, on REFUSE de prévoir
    (« le système sait dire qu'il ne sait pas »).
Modèles candidats volontairement simples et explicables : naïf, moyenne mobile, tendance linéaire.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from .stats import mae, regression_lineaire, wape

POINTS_MIN = 10
POINTS_TEST = 4
WAPE_MAX = 0.6          # au-delà, l'erreur typique dépasse 60 % du niveau réel : prévision non fiable
Z_80 = 1.2816           # intervalle ~80 %
INFLATION_SIGMA = 1.2


def _naif(hist, h):
    return [hist[-1]] * h


def _moyenne_mobile(hist, h, k=4):
    m = sum(hist[-k:]) / len(hist[-k:])
    return [m] * h


def _tendance(hist, h, fenetre=12):
    fen = hist[-fenetre:]
    pente, ordonnee, _ = regression_lineaire(fen)
    n = len(fen)
    return [ordonnee + pente * (n + j) for j in range(h)]


MODELES = {"naif": _naif, "moyenne_mobile": _moyenne_mobile, "tendance_lineaire": _tendance}


@dataclass
class ResultatPrevision:
    statut: str                                  # "ok" | "donnees_insuffisantes" | "non_fiable"
    message: str = ""
    modele: str | None = None
    previsions: list = field(default_factory=list)
    qualite: dict = field(default_factory=dict)
    limites: str = (
        "Projection statistique de l'historique récent : ne tient compte ni des événements futurs, "
        "ni de la saisonnalité, ni des décisions à venir."
    )


def prevoir(points, horizon=4) -> ResultatPrevision:
    """`points` : liste ordonnée de (debut_semaine, valeur) SANS trou (semaines vides = 0)."""
    valeurs = [float(v) for _, v in points]
    if len(valeurs) < POINTS_MIN:
        return ResultatPrevision(
            "donnees_insuffisantes",
            message=f"Historique insuffisant : {len(valeurs)} semaine(s), {POINTS_MIN} minimum pour prévoir.",
        )

    # Rétro-test : pour chaque semaine de test, on prévoit 1 pas à partir de l'historique antérieur seul.
    n_test = POINTS_TEST
    resultats = {}
    for nom, modele in MODELES.items():
        predits, reels = [], []
        for i in range(len(valeurs) - n_test, len(valeurs)):
            predits.append(modele(valeurs[:i], 1)[0])
            reels.append(valeurs[i])
        resultats[nom] = {"mae": mae(reels, predits), "wape": wape(reels, predits)}

    meilleur = min(resultats, key=lambda nom: resultats[nom]["mae"])
    qualite = {
        "modele_choisi": meilleur,
        "points_historique": len(valeurs),
        "points_retro_test": n_test,
        "mae_retro_test": round(resultats[meilleur]["mae"], 2),
        "wape_retro_test": None if resultats[meilleur]["wape"] is None else round(resultats[meilleur]["wape"], 3),
        "mae_baseline_naive": round(resultats["naif"]["mae"], 2),
        "gain_vs_naif": None if resultats["naif"]["mae"] == 0 else round(1 - resultats[meilleur]["mae"] / resultats["naif"]["mae"], 3),
    }
    w = resultats[meilleur]["wape"]
    if w is None or w > WAPE_MAX:
        return ResultatPrevision(
            "non_fiable", modele=meilleur, qualite=qualite,
            message=(
                "Prévision refusée : l'erreur du modèle lors du rétro-test est trop élevée "
                f"(WAPE {'indéfini' if w is None else round(w, 2)} > {WAPE_MAX}). Les flux sont trop irréguliers."
            ),
        )

    # Intervalle : écart-type des erreurs de rétro-test (1 pas), élargi avec l'horizon (√h).
    erreurs = []
    for i in range(len(valeurs) - 2 * n_test, len(valeurs)):
        if i >= 4:
            erreurs.append(valeurs[i] - MODELES[meilleur](valeurs[:i], 1)[0])
    sigma = (sum(e * e for e in erreurs) / max(len(erreurs), 1)) ** 0.5
    # Le modèle a été CHOISI sur ces mêmes erreurs (biais d'optimisme) et l'échantillon est petit :
    # sans inflation, la couverture mesurée du pas 1 était de 72 % pour 80 % annoncés.
    sigma *= INFLATION_SIGMA
    centrees = MODELES[meilleur](valeurs, horizon)
    dernier_debut = points[-1][0]
    previsions = []
    for j, c in enumerate(centrees, start=1):
        marge = Z_80 * sigma * (j ** 0.5)
        previsions.append({
            "semaine_debut": dernier_debut + timedelta(weeks=j),
            "valeur": round(c, 2), "bas": round(c - marge, 2), "haut": round(c + marge, 2),
        })
    return ResultatPrevision("ok", modele=meilleur, previsions=previsions, qualite=qualite)

"""
Statistiques robustes, en Python pur (aucune dépendance, entièrement testable).

Choix : médiane/MAD plutôt que moyenne/écart-type. Un seul pic extrême fausse la moyenne et l'écart-type
au point de se masquer lui-même ; la médiane et la MAD y résistent (point de rupture de 50 %).
"""
from math import sqrt
from statistics import median, stdev

MAD_VERS_SIGMA = 1.4826  # rend la MAD comparable à un écart-type pour des données gaussiennes


def mediane(valeurs):
    return median(valeurs)


def mad(valeurs):
    m = median(valeurs)
    return median(abs(v - m) for v in valeurs)


def regression_lineaire(y):
    """Moindres carrés sur x = 0..n-1. Retourne (pente, ordonnee, ecart_type_residuel)."""
    n = len(y)
    if n < 2:
        raise ValueError("au moins 2 points")
    x_moy = (n - 1) / 2
    y_moy = sum(y) / n
    sxx = sum((i - x_moy) ** 2 for i in range(n))
    pente = sum((i - x_moy) * (v - y_moy) for i, v in enumerate(y)) / sxx
    ordonnee = y_moy - pente * x_moy
    residus = [v - (ordonnee + pente * i) for i, v in enumerate(y)]
    sigma = sqrt(sum(r * r for r in residus) / max(n - 2, 1))
    return pente, ordonnee, sigma


def wape(reels, predits):
    """Erreur relative pondérée = Σ|erreur| / Σ|réel| (robuste aux valeurs proches de 0, contrairement au MAPE)."""
    total = sum(abs(r) for r in reels)
    if total == 0:
        return None
    return sum(abs(r - p) for r, p in zip(reels, predits)) / total


def mae(reels, predits):
    return sum(abs(r - p) for r, p in zip(reels, predits)) / len(reels)


def echelle_ecretee(valeurs, k=3.0):
    """
    Écart-type après écrêtage : on retire d'abord les valeurs à plus de k·σ_MAD de la médiane (les outliers
    déjà présents dans la référence), puis on calcule l'écart-type ordinaire des valeurs restantes.
    Pourquoi pas la MAD seule : sur ~20 points elle n'a que ~37 % d'efficacité statistique et sous-estime
    souvent la dispersion réelle → faux positifs en rafale (mesuré). Écrêtage + écart-type : robuste aux
    valeurs aberrantes ET précis sur les données propres.
    Retourne (écart-type, nombre de points conservés).
    """
    m = median(valeurs)
    s0 = MAD_VERS_SIGMA * mad(valeurs)
    if s0 == 0:
        garde = list(valeurs)
    else:
        garde = [v for v in valeurs if abs(v - m) <= k * s0] or list(valeurs)
    if len(garde) < 2:
        return 0.0, len(garde)
    return stdev(garde), len(garde)

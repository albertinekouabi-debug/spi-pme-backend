"""
Détection d'anomalies sur une série hebdomadaire (cœur pur, sans accès base).

Méthode : score z modifié (médiane/MAD) contre une fenêtre de référence GLISSANTE des semaines
précédentes. Garde-fous contre les faux positifs (une IA qui crie au loup perd toute valeur) :
  - historique minimal (sinon : « données insuffisantes », jamais une conclusion) ;
  - seuil z élevé ET écart relatif minimal par rapport à la médiane ;
  - séries trop intermittentes (majorité de semaines à zéro) ignorées ;
  - série parfaitement constante : échelle plancher plutôt qu'une division par zéro.
"""
from dataclasses import dataclass, field
from math import log1p

from math import sqrt

from .stats import echelle_ecretee, mediane

HISTORIQUE_MIN = 8
FENETRE_REFERENCE = 20
SEUIL_Z = 4.0
ECART_RELATIF_MIN = 0.5
SEUIL_Z_FORT = 6.0
# Valeurs mesurées (voir tests/test_qualite_reference.py) : ces réglages viennent d'un balayage, pas d'une
# intuition. Les premiers réglages (fenêtre 12, z 3,5, écart 25 %) donnaient une fausse alerte sur 44 % des
# séries de 40 semaines de bruit pur.


@dataclass
class Anomalie:
    periode_debut: object
    valeur: float
    reference_mediane: float
    ecart: float
    ecart_relatif: float | None
    score: float
    sens: str                      # "hausse" | "baisse"
    niveau_confiance: str          # "moyen" | "eleve"
    nb_points_reference: int
    methode: str = "z modifié (médiane/MAD), fenêtre glissante"


@dataclass
class ResultatAnomalies:
    statut: str                    # "ok" | "donnees_insuffisantes"
    anomalies: list[Anomalie] = field(default_factory=list)
    message: str = ""


def detecter(points, *, historique_min=HISTORIQUE_MIN, fenetre=FENETRE_REFERENCE,
             seuil_z=SEUIL_Z, ecart_relatif_min=ECART_RELATIF_MIN) -> ResultatAnomalies:
    """`points` : liste ordonnée de (debut_periode, valeur). Les semaines sans activité doivent valoir 0."""
    if len(points) < historique_min + 1:
        return ResultatAnomalies(
            "donnees_insuffisantes",
            message=f"Historique insuffisant : {len(points)} semaine(s), {historique_min + 1} minimum pour conclure.",
        )
    valeurs = [float(v) for _, v in points]
    # Un flux monétaire est positif et multiplicatif : en échelle linéaire, une hausse « normale » de 60 %
    # passe pour une anomalie. On travaille donc en log1p quand la série est non négative (sinon brut).
    echelle_log = all(v >= 0 for v in valeurs)
    espace = [log1p(v) for v in valeurs] if echelle_log else valeurs
    trouvees = []
    for i in range(historique_min, len(points)):
        ref = valeurs[max(0, i - fenetre):i]
        if sum(1 for v in ref if v == 0) > len(ref) / 2:
            continue  # trop intermittent : une reprise d'activité n'est pas une anomalie
        med = mediane(ref)
        ref_espace = espace[max(0, i - fenetre):i]
        med_espace = mediane(ref_espace)
        sigma, n_gardes = echelle_ecretee(ref_espace)
        # Échelle plancher : une série quasi constante ne doit pas produire de score infini.
        sigma = max(sigma, 0.05 if echelle_log else max(0.05 * abs(med_espace), 1e-9))
        # Intervalle de prédiction d'une NOUVELLE observation : l'incertitude de la référence s'ajoute (√(1+1/n)).
        echelle = sigma * sqrt(1 + 1 / max(n_gardes, 1))
        score = (espace[i] - med_espace) / echelle
        ecart = valeurs[i] - med
        # Double condition : statistiquement improbable ET économiquement significatif.
        if abs(score) < seuil_z or abs(ecart) < ecart_relatif_min * abs(med):
            continue
        trouvees.append(Anomalie(
            periode_debut=points[i][0], valeur=valeurs[i], reference_mediane=med, ecart=ecart,
            ecart_relatif=(ecart / med) if med else None, score=round(score, 2),
            sens="hausse" if ecart > 0 else "baisse",
            niveau_confiance="eleve" if abs(score) >= SEUIL_Z_FORT and len(ref) >= fenetre else "moyen",
            nb_points_reference=len(ref),
        ))
    return ResultatAnomalies("ok", trouvees)

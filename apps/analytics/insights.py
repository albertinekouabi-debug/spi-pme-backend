"""
Génération d'insights : chaque insight est assemblé à partir de FAITS vérifiables (KPI, anomalies, prévision),
par des gabarits déterministes. Séparation stricte FAIT ≠ PROJECTION ≠ RECOMMANDATION :
  - `faits`          : valeurs mesurées, avec leur source ;
  - `projection`     : calcul conditionnel explicite (« si ce rythme se maintient »), jamais présenté comme acquis ;
  - `recommandation` : options + justification liée aux faits ; aucune valeur d'impact inventée.
Aucun LLM ici : le moteur ne peut pas halluciner un chiffre.
"""
from datetime import date
from decimal import Decimal

from . import anomalies as moteur_anomalies
from . import kpi as moteur_kpi
from . import previsions as moteur_previsions
from . import series

VERSION_MOTEUR = "analytics-1.0"
SEUIL_VARIATION_ATTENTION = 20.0


def _fait(libelle, valeur, source):
    return {"libelle": libelle, "valeur": valeur, "source": source}


def _insight(code, categorie, niveau, titre, faits, analyse, *, projection=None, options=None, confiance, limites):
    return {
        "code": code, "categorie": categorie, "niveau": niveau, "titre": titre, "faits": faits,
        "analyse": analyse, "projection": projection, "recommandation": options, "confiance": confiance,
        "limites": limites, "version_moteur": VERSION_MOTEUR,
    }


def generer(secteur, aujourdhui: date):
    kpis = moteur_kpi.calculer_kpis(secteur, aujourdhui)
    insights = []

    # 1. Évolution des flux — uniquement si la base de comparaison est suffisante.
    for cle, libelle, croissance_mauvaise in (("variation_sorties_pct", "sorties", True), ("variation_entrees_pct", "entrées", False)):
        variation = kpis[cle]
        if variation is None:
            continue
        mesure = kpis["sorties_30j" if croissance_mauvaise else "entrees_30j"]
        mauvaise = (variation > 0) == croissance_mauvaise
        niveau = "attention" if abs(variation) >= SEUIL_VARIATION_ATTENTION and mauvaise else "info"
        sens = "augmenté" if variation > 0 else "diminué"
        insights.append(_insight(
            f"flux_{libelle}", "tresorerie", niveau, f"Les {libelle} ont {sens} de {abs(variation)} %",
            [_fait(f"{libelle.capitalize()} sur 30 jours", f'{mesure["valeur"]} XOF', f'{mesure["nb_elements"]} transactions validées'),
             _fait("Variation vs 30 jours précédents", f"{variation} %", f'périodes {kpis["periode_precedente"]["debut"]} → {kpis["periode_precedente"]["fin"]}')],
            f"Sur les 30 derniers jours, les {libelle} ont {sens} de {abs(variation)} % par rapport aux 30 jours précédents.",
            confiance="mesure", limites="Comparaison de deux fenêtres de 30 jours ; ne distingue pas une variation saisonnière d'une tendance.",
        ))

    # 2. Flux net négatif : projection ARITHMÉTIQUE conditionnelle, étiquetée comme telle.
    net = kpis["flux_net_30j"]
    if net["valeur"] is not None and Decimal(net["valeur"]) < 0:
        solde = Decimal(kpis["solde_cumule"]["valeur"] or 0)
        projete = solde + Decimal(net["valeur"])
        insights.append(_insight(
            "flux_net_negatif", "tresorerie", "attention", "Les sorties dépassent les entrées sur 30 jours",
            [_fait("Flux net 30 jours", f'{net["valeur"]} XOF', f'{net["nb_elements"]} transactions validées'),
             _fait("Solde cumulé enregistré", f"{solde} XOF", "entrées − sorties validées depuis l'origine (≠ solde bancaire)")],
            "Le flux net des 30 derniers jours est négatif : la trésorerie enregistrée diminue.",
            projection={"nature": "PROJECTION conditionnelle (arithmétique, pas une prévision statistique)",
                        "enonce": f"Si le flux net des 30 prochains jours est identique, le solde cumulé enregistré passerait de {solde} à {projete} XOF."},
            options=[{"option": "Examiner les principales sorties du mois", "justification": "Le flux net négatif vient des sorties ou d'un recul des entrées ; l'identifier précède toute décision."},
                     {"option": "Accélérer l'encaissement des créances en retard", "justification": "Voir l'insight sur les créances s'il existe."}],
            confiance="mesure", limites="Ne tient compte ni des factures à venir ni des échéances de paiement non encore saisies.",
        ))

    # 3. Créances en retard.
    retard = kpis["creances_en_retard"]
    if retard["nb_elements"]:
        debiteurs = moteur_kpi.principaux_debiteurs(secteur, aujourdhui)
        taux = kpis["taux_creances_en_retard_pct"]
        insights.append(_insight(
            "creances_en_retard", "creances", "attention" if (taux or 0) >= 30 else "info",
            f'{retard["nb_elements"]} facture(s) échue(s) non réglée(s)',
            [_fait("Montant en retard (net d'avoirs)", f'{retard["valeur"]} XOF', f'{retard["nb_elements"]} factures'),
             _fait("Part des créances ouvertes", f"{taux} %", f'sur {kpis["creances_ouvertes"]["valeur"]} XOF ouverts'),
             _fait("Principaux débiteurs", "; ".join(f"{nom} ({montant} XOF)" for nom, montant in debiteurs), "factures échues par client")],
            f'{taux} % des créances ouvertes ont dépassé leur échéance.',
            options=[{"option": f"Relancer en priorité {debiteurs[0][0]}" if debiteurs else "Relancer les clients concernés",
                      "justification": "C'est le débiteur au plus gros montant échu."},
                     {"option": "Proposer un échéancier aux débiteurs importants", "justification": "Limite le risque de non-recouvrement quand le retard est ancien."}],
            confiance="mesure", limites="Les factures sans date d'échéance ne sont pas comptées comme échues.",
        ))

    # 4. Stocks.
    if kpis["ressources_critiques"]["nb"]:
        insights.append(_insight(
            "stocks_critiques", "stocks", "critique", f'{kpis["ressources_critiques"]["nb"]} ressource(s) au niveau critique',
            [_fait("Ressources critiques", kpis["ressources_critiques"]["nb"], "statut calculé à partir des seuils configurés"),
             _fait("Ressources à surveiller", kpis["ressources_a_surveiller"]["nb"], "idem")],
            "Au moins une ressource est sous son seuil critique.",
            options=[{"option": "Valider les suggestions de réapprovisionnement en attente", "justification": "Elles sont générées à partir des seuils et de la tendance de consommation."}],
            confiance="mesure", limites="Dépend de la qualité des seuils saisis.",
        ))

    # 5. Anomalies sur les flux hebdomadaires (sorties, entrées).
    for types, libelle in ((["sortie"], "sorties"), (["entree"], "entrées")):
        res = moteur_anomalies.detecter(series.serie_hebdomadaire(secteur, types, aujourdhui))
        for a in res.anomalies[-2:]:  # les plus récentes seulement : pas de bruit historique
            insights.append(_insight(
                f"anomalie_{libelle}", "anomalie", "attention" if a.niveau_confiance == "eleve" else "info",
                f"Semaine inhabituelle ({libelle}) : {a.sens} marquée",
                [_fait("Semaine du", a.periode_debut.isoformat(), "séries hebdomadaires lundi→dimanche"),
                 _fait("Valeur observée", f"{round(a.valeur)} XOF", "transactions validées"),
                 _fait("Médiane des semaines de référence", f"{round(a.reference_mediane)} XOF", f"{a.nb_points_reference} semaines"),
                 _fait("Score", a.score, a.methode)],
                f"Les {libelle} de cette semaine s'écartent de {round(abs(a.ecart_relatif) * 100) if a.ecart_relatif is not None else '?'} % de la médiane récente, bien au-delà de la variabilité habituelle.",
                confiance=a.niveau_confiance,
                limites="Un écart statistique n'est pas une erreur : vérifier s'il correspond à un événement connu (achat exceptionnel, fin de mois...).",
            ))

    # 6. Prévision — ou aveu d'impuissance.
    prev = moteur_previsions.prevoir(series.serie_flux_net(secteur, aujourdhui), horizon=4)
    if prev.statut == "ok":
        total = sum(p["valeur"] for p in prev.previsions)
        insights.append(_insight(
            "prevision_flux_net", "prevision", "info", "Prévision du flux net sur 4 semaines",
            [_fait("Modèle retenu", prev.modele, "rétro-test sur les 4 dernières semaines"),
             _fait("Erreur relative du rétro-test (WAPE)", prev.qualite["wape_retro_test"], "plus bas = meilleur")],
            f"Flux net cumulé attendu sur 4 semaines : {round(total)} XOF (intervalle semaine par semaine fourni).",
            projection={"nature": "PRÉVISION statistique (jamais un fait)", "semaines": [
                {**p, "semaine_debut": p["semaine_debut"].isoformat()} for p in prev.previsions]},
            confiance="moyenne", limites=prev.limites,
        ))
    else:
        insights.append(_insight(
            "prevision_indisponible", "prevision", "info", "Prévision non disponible",
            [_fait("Raison", prev.message, "contrôles de qualité du moteur")],
            "Information insuffisante pour prévoir de façon fiable : aucune prévision n'est affichée plutôt qu'une prévision trompeuse.",
            confiance="n/a", limites=prev.message,
        ))
    ordre = {"critique": 0, "attention": 1, "info": 2}
    return sorted(insights, key=lambda i: ordre[i["niveau"]]), kpis, prev

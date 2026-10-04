"""
Copilote de pilotage — niveau 1, DÉTERMINISTE (classification : DISTANTE, exige le serveur).

La question est ramenée à une intention par règles de mots-clés ; la réponse est assemblée à partir des
moteurs (KPI, insights, anomalies, prévision). Conséquences voulues :
  - aucune hallucination possible sur les chiffres ; chaque réponse cite ses sources ;
  - une question hors périmètre reçoit « je ne peux pas répondre » + les questions supportées ;
  - le texte de l'utilisateur n'est jamais interprété comme une instruction (pas d'injection de prompt) ;
  - le périmètre sectoriel est celui de l'utilisateur (résolu par la vue, pas par la question).
Une couche LLM pourra ensuite REFORMULER ces réponses structurées, sans jamais produire de chiffres.
"""
import unicodedata

from . import insights as moteur_insights
from . import kpi as moteur_kpi

QUESTIONS_SUPPORTEES = [
    "Quelle est la situation actuelle de mon entreprise ?",
    "Qu'est-ce qui nécessite mon attention ?",
    "Quels sont mes principaux risques ?",
    "Quelles anomalies ont été détectées ?",
    "Que prévoient les tendances actuelles ?",
    "Quels clients deviennent inactifs ?",
]

INTENTIONS = [
    ("marge", ["marge", "rentabilite", "benefice"]),
    ("clients_inactifs", ["inactif"]),
    ("anomalies", ["anomal", "inhabituel", "bizarre"]),
    ("previsions", ["prevoi", "prevision", "tendance", "prochain", "futur"]),
    ("risques", ["risque", "danger", "menace"]),
    ("attention", ["attention", "priorit", "urgent", "important"]),
    ("situation", ["situation", "bilan", "resume", "comment va", "etat de"]),
]


def _normaliser(texte):
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", texte.lower()) if unicodedata.category(c) != "Mn")
    return sans_accents


def detecter_intention(question):
    q = _normaliser(question)
    for intention, mots in INTENTIONS:
        if any(m in q for m in mots):
            return intention
    return None


def repondre(question, secteur, aujourdhui):
    intention = detecter_intention(question)
    base = {"intention": intention, "classification": "DISTANTE", "questions_supportees": QUESTIONS_SUPPORTEES}

    if intention is None:
        return {**base, "statut": "non_compris",
                "reponse": "Je ne peux pas répondre à cette question avec les données et analyses disponibles."}
    if intention == "marge":
        return {**base, "statut": "information_insuffisante",
                "reponse": "Information insuffisante pour conclure : la marge exige le coût des ventes, qui n'est pas saisi dans SPI-PME (seuls les flux d'entrées et de sorties le sont)."}

    insights, kpis, prevision = moteur_insights.generer(secteur, aujourdhui)

    if intention == "clients_inactifs":
        inactifs = moteur_kpi.clients_inactifs(secteur, aujourdhui)
        if not inactifs:
            return {**base, "statut": "ok", "reponse": "Aucun client ayant déjà acheté au moins deux fois n'est inactif depuis plus de 90 jours.", "donnees": []}
        lignes = [{"client": e.nom, "derniere_transaction": e.derniere.date().isoformat(), "nb_transactions": e.nb,
                   "total_entrees": str(e.total or 0)} for e in inactifs]
        return {**base, "statut": "ok", "donnees": lignes,
                "reponse": f"{len(lignes)} client(s) sans transaction depuis plus de 90 jours : " + "; ".join(l["client"] for l in lignes) + ".",
                "sources": "transactions validées par client"}

    if intention == "anomalies":
        sel = [i for i in insights if i["categorie"] == "anomalie"]
        return {**base, "statut": "ok", "donnees": sel,
                "reponse": f"{len(sel)} anomalie(s) récente(s) détectée(s)." if sel else "Aucune anomalie détectée (ou historique insuffisant pour en détecter)."}
    if intention == "previsions":
        sel = [i for i in insights if i["categorie"] == "prevision"]
        return {**base, "statut": "ok", "donnees": sel, "reponse": sel[0]["analyse"]}
    if intention == "risques":
        sel = [i for i in insights if i["code"] in ("creances_en_retard", "stocks_critiques", "flux_net_negatif")]
        return {**base, "statut": "ok", "donnees": sel,
                "reponse": " ".join(i["titre"] + "." for i in sel) if sel else "Aucun risque identifié par les règles actuelles (créances, stocks, flux net)."}
    if intention == "attention":
        sel = [i for i in insights if i["niveau"] in ("critique", "attention")]
        return {**base, "statut": "ok", "donnees": sel,
                "reponse": " ".join(i["titre"] + "." for i in sel) if sel else "Rien ne nécessite votre attention immédiate d'après les règles actuelles."}

    # situation
    net, ent, sor = kpis["flux_net_30j"], kpis["entrees_30j"], kpis["sorties_30j"]
    if net["statut"] != "ok":
        reponse = "Information insuffisante : aucune transaction validée sur les 30 derniers jours."
    else:
        reponse = (f"Sur 30 jours : entrées {ent['valeur']} XOF, sorties {sor['valeur']} XOF, flux net {net['valeur']} XOF. "
                   f"Créances en retard : {kpis['creances_en_retard']['valeur'] or 0} XOF. "
                   f"Ressources critiques : {kpis['ressources_critiques']['nb']}.")
    return {**base, "statut": "ok", "reponse": reponse, "donnees": {"kpis": kpis, "insights": [i for i in insights if i["niveau"] != "info"]}}

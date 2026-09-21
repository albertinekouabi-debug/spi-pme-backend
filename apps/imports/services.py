"""
Deux modes, un seul chemin de code :
  - `previsualiser()` : parse + valide, ne touche à aucune table (étapes
    "Valider"/"Aperçu" de la maquette).
  - `importer()` : parse + valide + persiste (étapes "Importer"/"Terminé").

Décision de conception : les lignes valides sont importées même si d'autres
lignes du même fichier sont en erreur (import partiel, statut
"termine_avec_anomalies") — c'est ce que montre la maquette (528 lignes,
12 erreurs, import quand même utile pour les 516 lignes correctes). Un
rollback complet à la première erreur serait plus strict mais rendrait le
rapport d'anomalies inutile.
"""
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.resources.models import Ressource

from .models import ImportFichier
from .parsers import parser_fichier
from .validators import valider_ligne

TAILLE_APERCU = 10


def _analyser(nom_fichier: str, contenu: bytes) -> dict:
    lignes = parser_fichier(nom_fichier, contenu)
    rapport_anomalies = []
    lignes_valides = []

    for index, ligne in enumerate(lignes, start=1):
        normalisee, erreur = valider_ligne(ligne)
        if erreur:
            rapport_anomalies.append({"ligne": index, **erreur})
        else:
            lignes_valides.append((index, normalisee))

    return {
        "lignes": lignes,
        "lignes_valides": lignes_valides,
        "rapport_anomalies": rapport_anomalies,
    }


def previsualiser(nom_fichier: str, contenu: bytes) -> dict:
    analyse = _analyser(nom_fichier, contenu)
    return {
        "lignes_totales": len(analyse["lignes"]),
        "lignes_valides": len(analyse["lignes_valides"]),
        "lignes_rejetees": len(analyse["rapport_anomalies"]),
        "rapport_anomalies": analyse["rapport_anomalies"],
        "apercu": analyse["lignes"][:TAILLE_APERCU],
    }


def _upsert_ressource(secteur, donnees: dict) -> None:
    """
    Rapproche par (secteur, nom) insensible à la casse : une ressource déjà
    connue voit son niveau/sa valeur mis à jour (les seuils déjà configurés
    sont conservés) ; une ressource inconnue est créée sans seuils (voir
    apps.resources.models.Ressource.calculer_statut — statut "stable" tant
    qu'ils ne sont pas configurés manuellement).
    """
    ressource = Ressource.objects.filter(secteur=secteur, nom__iexact=donnees["nom"]).first()
    if ressource is None:
        ressource = Ressource(secteur=secteur, nom=donnees["nom"], type=donnees["categorie"])

    ressource.niveau_actuel = donnees["quantite"]
    if donnees["unite"]:
        ressource.unite = donnees["unite"]
    if donnees["emplacement"]:
        ressource.emplacement = donnees["emplacement"]
    if donnees["valeur_unitaire"] is not None:
        ressource.valeur_unitaire = donnees["valeur_unitaire"]
    if donnees["seuil_critique"] is not None:
        ressource.seuil_critique = donnees["seuil_critique"]
    if donnees["seuil_alerte"] is not None:
        ressource.seuil_alerte = donnees["seuil_alerte"]
    ressource.save()


def importer(nom_fichier: str, contenu: bytes, secteur, auteur) -> ImportFichier:
    analyse = _analyser(nom_fichier, contenu)
    extension = nom_fichier.rsplit(".", 1)[-1].lower() if "." in nom_fichier else "csv"

    import_fichier = ImportFichier.objects.create(
        nom_fichier=nom_fichier,
        type_fichier=extension if extension in ("csv", "xlsx", "xls") else "csv",
        taille_octets=len(contenu),
        statut="en_cours",
        lignes_totales=len(analyse["lignes"]),
        auteur=auteur,
        secteur=secteur,
    )

    lignes_importees = 0
    for _, donnees in analyse["lignes_valides"]:
        with db_transaction.atomic():
            _upsert_ressource(secteur, donnees)
        lignes_importees += 1

    rapport_anomalies = analyse["rapport_anomalies"]
    if lignes_importees == 0 and rapport_anomalies:
        statut = "echec"
    elif rapport_anomalies:
        statut = "termine_avec_anomalies"
    else:
        statut = "termine"

    import_fichier.lignes_importees = lignes_importees
    import_fichier.lignes_rejetees = len(rapport_anomalies)
    import_fichier.rapport_anomalies = rapport_anomalies
    import_fichier.statut = statut
    import_fichier.date_fin = timezone.now()
    import_fichier.save()

    from apps.audit.services import enregistrer
    enregistrer(
        action="import_fichier", module="imports",
        resultat="echec" if statut == "echec" else ("avertissement" if rapport_anomalies else "reussi"),
        auteur=auteur, cible=import_fichier,
        details={"nom_fichier": nom_fichier, "lignes_importees": lignes_importees, "lignes_rejetees": len(rapport_anomalies)},
    )
    return import_fichier

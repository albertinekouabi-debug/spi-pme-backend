"""
Réconciliation : à chaque exécution, l'état "souhaité" (ce que les détecteurs
observent maintenant) est comparé à l'état "actuel" (les alertes déjà actives
en base). Les alertes actives dont la condition a disparu sont résolues
automatiquement — ex. une rupture de stock traitée entre deux exécutions ne
doit pas laisser une alerte "active" orpheline.
"""
from django.db import transaction as db_transaction
from django.utils import timezone

from .detectors import REGISTRE
from .models import Alerte


class DecisionAlerteError(Exception):
    """Levée quand une action est tentée sur une alerte déjà traitée/ignorée."""


def generer_pour_secteur(secteur) -> dict:
    """Retourne {"creees": [...], "resolues_automatiquement": [...]}."""
    creees: list[Alerte] = []
    resolues: list[Alerte] = []

    for type_code, detecteur_class in REGISTRE.items():
        drafts = detecteur_class().detecter(secteur)
        cles_actuelles = {d.cle_objet for d in drafts}

        alertes_actives = Alerte.objects.filter(secteur=secteur, type=type_code, statut="active")
        for alerte in alertes_actives:
            cle_alerte = alerte.ressource_id or alerte.entite_id or alerte.tache_id or alerte.facture_id
            if cle_alerte not in cles_actuelles:
                alerte.statut = "traitee"
                alerte.date_resolution = timezone.now()
                alerte.save(update_fields=["statut", "date_resolution"])
                resolues.append(alerte)

        cles_deja_actives = set(
            alertes_actives.exclude(pk__in=[a.pk for a in resolues])
            .values_list("ressource_id", "entite_id", "tache_id", "facture_id")
        )
        cles_deja_actives_aplaties = {next((v for v in tup if v is not None), None) for tup in cles_deja_actives}

        for draft in drafts:
            if draft.cle_objet in cles_deja_actives_aplaties:
                continue
            alerte = Alerte.objects.create(
                type=draft.type, niveau=draft.niveau, titre=draft.titre, description=draft.description,
                ressource_id=draft.ressource_id, entite_id=draft.entite_id,
                tache_id=draft.tache_id, facture_id=draft.facture_id,
                secteur=secteur,
            )
            creees.append(alerte)
            cles_deja_actives_aplaties.add(draft.cle_objet)

    return {"creees": creees, "resolues_automatiquement": resolues}


@db_transaction.atomic
def _decider(alerte_id: int, nouveau_statut: str, utilisateur=None) -> Alerte:
    alerte = Alerte.objects.select_for_update().get(pk=alerte_id)
    if alerte.statut != "active":
        raise DecisionAlerteError("Cette alerte a déjà été traitée ou ignorée.")
    alerte.statut = nouveau_statut
    alerte.date_resolution = timezone.now()
    alerte.save(update_fields=["statut", "date_resolution"])

    from apps.audit.services import enregistrer
    enregistrer(
        action=f"alerte_{nouveau_statut}", module="alerts", auteur=utilisateur, cible=alerte,
        details={"titre": alerte.titre, "type": alerte.type},
    )
    return alerte


def traiter_alerte(alerte_id: int, utilisateur=None) -> Alerte:
    return _decider(alerte_id, "traitee", utilisateur=utilisateur)


def ignorer_alerte(alerte_id: int, utilisateur=None) -> Alerte:
    return _decider(alerte_id, "ignoree", utilisateur=utilisateur)

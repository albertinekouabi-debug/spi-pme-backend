"""
Couche service — c'est ICI, et nulle part ailleurs, que :
  1. les brouillons d'algorithmes deviennent des Suggestion persistées
     (avec déduplication : pas de suggestion en double pour la même
     ressource/algorithme tant qu'une précédente est encore en_attente) ;
  2. une validation peut produire une Transaction réelle (FR-SUG-03) ;
  3. la concurrence est maîtrisée via select_for_update() — deux décisions
     simultanées sur la même Suggestion ne peuvent pas créer deux Transaction.
"""
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from apps.resources.models import Ressource
from apps.treasury.models import Transaction

from .algorithms import REGISTRE
from .models import Suggestion


class DecisionSuggestionError(Exception):
    """Levée quand une décision (validation/rejet) est tentée sur une suggestion déjà tranchée."""


def generer_pour_secteur(secteur) -> list[Suggestion]:
    """
    Exécute tous les algorithmes enregistrés pour un secteur et persiste les
    suggestions nouvelles. Idempotent : si une suggestion en_attente existe
    déjà pour la même (ressource, type d'algorithme), aucun doublon n'est créé.
    """
    suggestions_creees: list[Suggestion] = []

    dejas_en_attente = set(
        Suggestion.objects.filter(secteur=secteur, statut="en_attente")
        .values_list("type_algorithme", "ressource_liee_id")
    )

    for code, algo_class in REGISTRE.items():
        algorithme = algo_class()
        for draft in algorithme.generer(secteur):
            cle = (code, draft.ressource_liee_id)
            if cle in dejas_en_attente:
                continue

            suggestion = Suggestion.objects.create(
                type_algorithme=code,
                categorie=draft.categorie,
                titre=draft.titre,
                description=draft.description,
                facteurs=draft.facteurs,
                impact_estime=draft.impact_estime,
                confiance=draft.confiance,
                ressource_liee_id=draft.ressource_liee_id,
                entite_liee_id=draft.entite_liee_id,
                secteur=secteur,
            )
            suggestions_creees.append(suggestion)
            dejas_en_attente.add(cle)  # évite un doublon si deux algorithmes visent la même ressource dans le même run

    return suggestions_creees


@db_transaction.atomic
def valider_suggestion(suggestion_id: int, decideur) -> Suggestion:
    """
    FR-SUG-03 : c'est la SEULE fonction du système autorisée à transformer une
    Suggestion validée en action réelle. select_for_update() verrouille la
    ligne pour la durée de la transaction : si deux requêtes de validation
    arrivent en même temps, la seconde attend, relit le statut déjà "validee"
    et échoue proprement plutôt que de créer une deuxième Transaction.
    """
    suggestion = Suggestion.objects.select_for_update().get(pk=suggestion_id)
    if suggestion.statut != "en_attente":
        raise DecisionSuggestionError("Cette suggestion a déjà été traitée.")

    transaction_resultante = None
    quantite_suggeree = suggestion.facteurs.get("quantite_suggeree")

    # Traçabilité : la quantité a été calculée à la GÉNÉRATION ; le stock a pu changer depuis.
    # On ne modifie pas la règle métier (l'humain valide ce qu'il a vu), mais l'écart est
    # journalisé pour qu'un réapprovisionnement devenu excessif soit visible à l'audit.
    ecart_stock = None
    if suggestion.ressource_liee_id and "niveau_actuel" in suggestion.facteurs:
        niveau_courant = Ressource.objects.filter(pk=suggestion.ressource_liee_id).values_list(
            "niveau_actuel", flat=True
        ).first()
        if niveau_courant is not None:
            ecart_stock = {
                "niveau_a_la_generation": suggestion.facteurs["niveau_actuel"],
                "niveau_a_la_validation": float(niveau_courant),
            }

    if suggestion.ressource_liee_id and quantite_suggeree:
        transaction_resultante = Transaction.objects.create(
            type="mouvement_stock",
            secteur=suggestion.secteur,
            ressource_id=suggestion.ressource_liee_id,
            quantite=Decimal(str(quantite_suggeree)),
            date_transaction=timezone.now(),
            description=f"Exécution de la suggestion IA #{suggestion.id} ({suggestion.titre})",
            auteur=decideur,
        )

    suggestion.statut = "validee"
    suggestion.decideur = decideur
    suggestion.date_decision = timezone.now()
    suggestion.transaction_resultante = transaction_resultante
    suggestion.save()

    from apps.audit.services import enregistrer
    enregistrer(
        action="validation_suggestion", module="intelligence", auteur=decideur, cible=suggestion,
        details={"titre": suggestion.titre, "type_algorithme": suggestion.type_algorithme,
                 "transaction_creee": transaction_resultante.id if transaction_resultante else None,
                 "stock": ecart_stock},
    )
    return suggestion


@db_transaction.atomic
def rejeter_suggestion(suggestion_id: int, decideur, motif: str) -> Suggestion:
    """
    §11.3 (étape 5, apprentissage) : le motif de rejet est obligatoire et
    conservé — c'est la matière première d'un futur raffinement des
    algorithmes (hors périmètre de ce module : aucun réentraînement
    automatique n'est fait ici, seulement la conservation de la donnée).
    """
    suggestion = Suggestion.objects.select_for_update().get(pk=suggestion_id)
    if suggestion.statut != "en_attente":
        raise DecisionSuggestionError("Cette suggestion a déjà été traitée.")

    suggestion.statut = "rejetee"
    suggestion.decideur = decideur
    suggestion.motif_decision = motif
    suggestion.date_decision = timezone.now()
    suggestion.save()

    from apps.audit.services import enregistrer
    enregistrer(
        action="rejet_suggestion", module="intelligence", auteur=decideur, cible=suggestion,
        details={"titre": suggestion.titre, "motif": motif},
    )
    return suggestion

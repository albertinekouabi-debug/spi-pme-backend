"""
Détection et gestion des obligations de déclaration CEMAC/COBAC.

Le seuil n'est JAMAIS une constante Python : il est lu depuis
ParametreSysteme à chaque évaluation. Si aucun paramètre n'est configuré
(instance non encore initialisée via seed_parametres_conformite), aucune
détection n'est effectuée plutôt que de retomber sur une valeur par défaut
inventée — voir `obtenir_seuil_especes`.
"""
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.core.models import ParametreSysteme

from .models import DeclarationConformite, Transaction

CLE_SEUIL_ESPECES = "conformite.seuil_declaration_especes_cemac"


class DeclarationConformiteError(Exception):
    pass


def obtenir_seuil_especes() -> Decimal | None:
    """
    Retourne le seuil actuellement configuré (FCFA), ou None s'il n'a pas été
    initialisé. Ne fabrique jamais de valeur par défaut : un seuil légal non
    configuré doit être visible comme un manque de configuration, pas masqué
    par un chiffre inventé.
    """
    parametre = ParametreSysteme.objects.filter(cle=CLE_SEUIL_ESPECES, secteur__isnull=True).first()
    if parametre is None:
        return None
    montant = parametre.valeur.get("montant")
    return Decimal(str(montant)) if montant is not None else None


def evaluer_transaction(transaction: Transaction) -> DeclarationConformite | None:
    """
    Appelée après la création d'une Transaction financière (entree/sortie).
    Crée une DeclarationConformite si les conditions légales sont réunies,
    sinon ne fait rien (pas d'obligation, ou déjà évaluée).
    """
    if transaction.type not in ("entree", "sortie"):
        return None
    if transaction.mode_paiement != "especes":
        return None
    if transaction.montant is None:
        return None
    if hasattr(transaction, "declaration_conformite"):
        return None  # déjà évaluée (idempotence)

    seuil = obtenir_seuil_especes()
    if seuil is None or transaction.montant < seuil:
        return None

    return DeclarationConformite.objects.create(
        transaction=transaction, motif="especes_superieur_seuil", seuil_applique=seuil,
    )


def signaler_manuellement(transaction_id: int, note: str) -> DeclarationConformite:
    """
    §CEMAC : « toute transaction qui paraît suspecte » doit être déclarée,
    quel qu'en soit le montant — pas seulement celles dépassant le seuil
    automatique. Signalement humain, jamais automatisé.
    """
    transaction = Transaction.objects.get(pk=transaction_id)
    if hasattr(transaction, "declaration_conformite"):
        raise DeclarationConformiteError("Cette transaction fait déjà l'objet d'une déclaration.")
    return DeclarationConformite.objects.create(
        transaction=transaction, motif="signalement_manuel", note=note,
    )


@db_transaction.atomic
def marquer_declaree(declaration_id: int, declarant, reference_declaration: str) -> DeclarationConformite:
    declaration = DeclarationConformite.objects.select_for_update().get(pk=declaration_id)
    if declaration.statut != "a_declarer":
        raise DeclarationConformiteError("Cette déclaration a déjà été traitée.")
    declaration.statut = "declaree"
    declaration.declarant = declarant
    declaration.reference_declaration = reference_declaration
    declaration.date_declaration = timezone.now()
    declaration.save()

    from apps.audit.services import enregistrer
    enregistrer(
        action="declaration_conformite_effectuee", module="treasury", auteur=declarant, cible=declaration,
        details={"reference_declaration": reference_declaration, "motif": declaration.motif},
    )
    return declaration


@db_transaction.atomic
def marquer_exemptee(declaration_id: int, declarant, note: str) -> DeclarationConformite:
    declaration = DeclarationConformite.objects.select_for_update().get(pk=declaration_id)
    if declaration.statut != "a_declarer":
        raise DeclarationConformiteError("Cette déclaration a déjà été traitée.")
    declaration.statut = "exemptee"
    declaration.declarant = declarant
    declaration.note = note
    declaration.date_declaration = timezone.now()
    declaration.save()

    from apps.audit.services import enregistrer
    enregistrer(
        action="declaration_conformite_exemptee", module="treasury", resultat="avertissement",
        auteur=declarant, cible=declaration, details={"note": note},
    )
    return declaration

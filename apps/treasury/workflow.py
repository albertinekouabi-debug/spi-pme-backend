"""
Workflow métier des corrections financières (politique de référence).

    brouillon ──valider──▶ validée ──contre-passer/corriger──▶ contre-passée (+ écriture inverse)
                              │
                              └──réouvrir (exceptionnel, permission + motif)──▶ brouillon

- Une transaction validée est IMMUABLE (gel imposé par Transaction.save et l'API).
- Une erreur se corrige par contre-écriture (montant/quantité opposés, même type) puis, si besoin,
  une nouvelle transaction. Rien n'est supprimé ni réécrit : chaîne append-only.
- Un mouvement de stock contre-passé rétablit automatiquement le stock (la contre-écriture est
  elle-même un mouvement validé).
- Facture émise : avoir (facture négative liée), jamais un PATCH.
- Période clôturée (Secteur.date_cloture) : aucune écriture/modification/réouverture datée dedans ;
  la régularisation est datée du jour, dans la période ouverte.

Toutes les opérations sont atomiques et journalisées (qui, quoi, quand, pourquoi, avant/après).
"""
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.services import enregistrer

from .models import DemandeReouverture, Facture, Transaction


class ErreurWorkflow(Exception):
    """Règle métier violée. `code_http` guide la vue (409 état incompatible, 403 droit, 400 donnée)."""

    def __init__(self, message: str, code_http: int = 409):
        super().__init__(message)
        self.message = message
        self.code_http = code_http


PERMISSION_REOUVERTURE = "treasury.reopen"


def _auditer(action, utilisateur, cible, *, ancien=None, nouveau=None, motif="", request_id=None, extra=None):
    details = {"ancien_etat": ancien, "nouvel_etat": nouveau, "motif": motif, "source": "api"}
    if request_id:
        details["request_id"] = request_id
    if extra:
        details.update(extra)
    enregistrer(action=action, module="treasury", auteur=utilisateur, cible=cible, details=details)


def periode_close(secteur, date) -> bool:
    cloture = secteur.date_cloture
    return cloture is not None and (date.date() if hasattr(date, "date") else date) <= cloture


def assurer_periode_ouverte(secteur, date):
    if periode_close(secteur, date):
        raise ErreurWorkflow(
            f"Période clôturée jusqu'au {secteur.date_cloture:%d/%m/%Y} : seule une régularisation "
            "datée dans la période ouverte est possible.",
            400,
        )


def _etat(t: Transaction) -> dict:
    return {"statut": t.statut, "type": t.type, "montant": str(t.montant) if t.montant is not None else None,
            "quantite": str(t.quantite) if t.quantite is not None else None}


# --------------------------------------------------------------------------- transactions
@db_transaction.atomic
def valider_transaction(transaction: Transaction, utilisateur, request_id=None) -> Transaction:
    transaction = Transaction.objects.select_for_update().get(pk=transaction.pk)
    if transaction.statut != "brouillon":
        raise ErreurWorkflow(f"Seul un brouillon peut être validé (statut actuel : {transaction.statut}).")
    assurer_periode_ouverte(transaction.secteur, transaction.date_transaction)
    ancien = _etat(transaction)
    transaction.statut = "validee"
    transaction.save()  # applique l'effet de stock (une seule fois)
    from . import compliance
    compliance.evaluer_transaction(transaction)
    _auditer("validation_transaction", utilisateur, transaction, ancien=ancien, nouveau=_etat(transaction),
             request_id=request_id)
    return transaction


def _creer_contre_ecriture(original: Transaction, utilisateur, motif: str) -> Transaction:
    inverse = Transaction(
        type=original.type,
        reference=f"CE-{original.reference or original.pk}"[:60],
        description=f"Contre-écriture de #{original.pk} : {motif}"[:255],
        montant=-original.montant if original.montant is not None else None,
        quantite=-original.quantite if original.quantite is not None else None,
        devise=original.devise,
        mode_paiement=original.mode_paiement,
        entite=original.entite,
        ressource=original.ressource,
        secteur=original.secteur,
        auteur=utilisateur,
        date_transaction=timezone.now(),  # régularisation : toujours datée dans la période ouverte
        statut="validee",
        contre_ecriture_de=original,
        motif_correction=motif,
        corrigee_par=utilisateur,
        date_correction=timezone.now(),
    )
    inverse.save()  # un mouvement de stock inverse rétablit le stock via Transaction.save()
    return inverse


@db_transaction.atomic
def contre_passer(transaction: Transaction, utilisateur, motif: str, request_id=None) -> Transaction:
    original = Transaction.objects.select_for_update().select_related("secteur").get(pk=transaction.pk)
    if original.statut != "validee":
        raise ErreurWorkflow(
            "Seule une transaction validée peut être contre-passée "
            f"(statut actuel : {original.get_statut_display()})."
        )
    if original.contre_ecriture_de_id:
        raise ErreurWorkflow("Une contre-écriture ne peut pas être elle-même contre-passée.")
    assurer_periode_ouverte(original.secteur, timezone.now())  # la régularisation doit tomber dans une période ouverte

    ancien = _etat(original)
    inverse = _creer_contre_ecriture(original, utilisateur, motif)
    original.statut = "contrepassee"
    original.save(update_fields=["statut"])
    _auditer("contre_passation_transaction", utilisateur, original, ancien=ancien, nouveau=_etat(original),
             motif=motif, request_id=request_id, extra={"contre_ecriture_id": inverse.pk})
    return inverse


@db_transaction.atomic
def corriger(transaction: Transaction, utilisateur, motif: str, donnees_remplacement: dict, request_id=None):
    """Contre-écriture + nouvelle transaction corrigée, atomiquement (tout ou rien)."""
    inverse = contre_passer(transaction, utilisateur, motif, request_id=request_id)
    original = Transaction.objects.get(pk=transaction.pk)
    nouvelle = Transaction(
        **donnees_remplacement, auteur=utilisateur, statut="validee",
        remplace=original, motif_correction=motif, corrigee_par=utilisateur, date_correction=timezone.now(),
    )
    assurer_periode_ouverte(nouvelle.secteur, nouvelle.date_transaction)
    nouvelle.save()
    _auditer("correction_transaction", utilisateur, original, nouveau={"remplacement_id": nouvelle.pk},
             motif=motif, request_id=request_id, extra={"contre_ecriture_id": inverse.pk})
    return inverse, nouvelle


def _peut_reouvrir(utilisateur) -> bool:
    return bool(
        utilisateur.role
        and (utilisateur.role.nom == "Administrateur" or utilisateur.a_les_permissions([PERMISSION_REOUVERTURE]))
    )


def _verifier_reouvrable(t: Transaction):
    """Préconditions communes (à la demande ET à l'approbation : l'état peut changer entre les deux)."""
    if t.statut != "validee":
        raise ErreurWorkflow(f"Seule une transaction validée peut être rouverte (statut : {t.get_statut_display()}).")
    if t.type == "mouvement_stock":
        raise ErreurWorkflow("Un mouvement de stock ne se rouvre pas : utiliser la contre-écriture (le stock est déjà impacté).")
    if t.contre_ecriture_de_id or t.contre_ecritures.exists():
        raise ErreurWorkflow("Cette transaction fait déjà partie d'une chaîne de correction.")
    assurer_periode_ouverte(t.secteur, t.date_transaction)


def _passer_en_brouillon(t: Transaction, utilisateur, motif, request_id, extra=None):
    ancien = _etat(t)
    t.statut = "brouillon"
    t.save(update_fields=["statut"])
    _auditer("reouverture_transaction", utilisateur, t, ancien=ancien, nouveau=_etat(t), motif=motif,
             request_id=request_id, extra=extra)


@db_transaction.atomic
def reouvrir(transaction: Transaction, utilisateur, motif: str, request_id=None) -> Transaction:
    """
    Réouverture DIRECTE (un seul acteur). Désactivée par défaut : avec REOUVERTURE_DOUBLE_VALIDATION=True
    (sûr), il faut passer par `demander_reouverture` puis `decider_reouverture` (deux personnes distinctes).
    """
    from django.conf import settings
    if getattr(settings, "REOUVERTURE_DOUBLE_VALIDATION", True):
        raise ErreurWorkflow(
            "Réouverture à double validation : faire une demande (/demander-reouverture/), "
            "qu'une AUTRE personne habilitée approuvera.", 409)
    if not _peut_reouvrir(utilisateur):
        raise ErreurWorkflow("La réouverture exige la permission « treasury.reopen ».", 403)
    t = Transaction.objects.select_for_update().select_related("secteur").get(pk=transaction.pk)
    _verifier_reouvrable(t)
    _passer_en_brouillon(t, utilisateur, motif, request_id, extra={"validation": "directe"})
    return t


@db_transaction.atomic
def demander_reouverture(transaction: Transaction, utilisateur, motif: str, request_id=None) -> DemandeReouverture:
    t = Transaction.objects.select_for_update().select_related("secteur").get(pk=transaction.pk)
    _verifier_reouvrable(t)
    if t.demandes_reouverture.filter(statut="en_attente").exists():
        raise ErreurWorkflow("Une demande de réouverture est déjà en attente pour cette transaction.")
    demande = DemandeReouverture.objects.create(transaction=t, demandeur=utilisateur, motif=motif)
    _auditer("demande_reouverture", utilisateur, t, motif=motif, request_id=request_id,
             extra={"demande_id": demande.pk})
    return demande


@db_transaction.atomic
def decider_reouverture(demande: DemandeReouverture, approbateur, approuver: bool, motif_decision: str = "",
                        request_id=None) -> DemandeReouverture:
    d = DemandeReouverture.objects.select_for_update().select_related("transaction").get(pk=demande.pk)
    if not _peut_reouvrir(approbateur):
        raise ErreurWorkflow("Approuver une réouverture exige la permission « treasury.reopen ».", 403)
    if d.demandeur_id == approbateur.pk:
        # Séparation des tâches : on n'approuve JAMAIS sa propre demande (même Administrateur).
        raise ErreurWorkflow("Le demandeur ne peut pas approuver sa propre demande de réouverture.", 403)
    if d.statut != "en_attente":
        raise ErreurWorkflow(f"Cette demande est déjà {d.get_statut_display().lower()}.")
    if not approuver and not motif_decision.strip():
        raise ErreurWorkflow("Un refus doit être motivé.", 400)

    d.approbateur, d.motif_decision, d.date_decision = approbateur, motif_decision, timezone.now()
    if approuver:
        t = Transaction.objects.select_for_update().select_related("secteur").get(pk=d.transaction_id)
        _verifier_reouvrable(t)  # l'état a pu changer depuis la demande (contre-passée, période close...)
        d.statut = "approuvee"
        _passer_en_brouillon(t, approbateur, d.motif, request_id,
                             extra={"validation": "double", "demande_id": d.pk, "demandeur_id": d.demandeur_id,
                                    "motif_decision": motif_decision})
    else:
        d.statut = "refusee"
    d.save()
    _auditer("decision_reouverture", approbateur, d.transaction, request_id=request_id, motif=motif_decision,
             extra={"demande_id": d.pk, "decision": d.statut, "demandeur_id": d.demandeur_id})
    return d


# --------------------------------------------------------------------------- factures / avoirs
def montant_deja_credite(facture: Facture) -> Decimal:
    total = facture.avoirs.aggregate(t=Sum("montant"))["t"] or Decimal("0")
    return -total  # les avoirs sont stockés en négatif


@db_transaction.atomic
def emettre_avoir(facture: Facture, utilisateur, motif: str, montant: Decimal | None = None,
                  request_id=None, cloturer_si_total: bool = True) -> Facture:
    origine = Facture.objects.select_for_update().select_related("secteur").get(pk=facture.pk)
    if origine.avoir_de_id or origine.statut == "avoir":
        raise ErreurWorkflow("Un avoir ne peut pas faire l'objet d'un avoir.")
    if origine.statut == "annulee":
        raise ErreurWorkflow("Cette facture est déjà entièrement créditée/annulée.")

    restant = origine.montant - montant_deja_credite(origine)
    montant = restant if montant is None else Decimal(montant)
    if montant <= 0:
        raise ErreurWorkflow("Le montant de l'avoir doit être strictement positif.", 400)
    if montant > restant:
        raise ErreurWorkflow(f"Montant de l'avoir supérieur au restant créditable ({restant}).", 400)

    rang = origine.avoirs.count() + 1
    numero = f"AV-{origine.numero}" + (f"-{rang}" if rang > 1 else "")
    avoir = Facture.objects.create(
        numero=numero, entite=origine.entite, transaction=origine.transaction, montant=-montant,
        taux_tva=origine.taux_tva, statut="avoir", secteur=origine.secteur, avoir_de=origine, motif_avoir=motif,
    )
    ancien = {"statut": origine.statut}
    if cloturer_si_total and montant == restant:
        origine.statut = "annulee"
        origine.motif_annulation = motif
        origine.annulee_par = utilisateur
        origine.date_annulation = timezone.now()
        origine.save(update_fields=["statut", "motif_annulation", "annulee_par", "date_annulation"])
    _auditer("emission_avoir", utilisateur, origine, ancien=ancien, nouveau={"statut": origine.statut},
             motif=motif, request_id=request_id,
             extra={"avoir_id": avoir.pk, "avoir_numero": avoir.numero, "montant_avoir": str(montant)})
    return avoir


def annuler_facture(facture: Facture, utilisateur, motif: str, request_id=None) -> Facture:
    """Annulation = avoir TOTAL relié + facture d'origine passée à « annulée » (jamais de suppression)."""
    emettre_avoir(facture, utilisateur, motif, montant=None, request_id=request_id)
    _auditer("annulation_facture", utilisateur, facture, motif=motif, request_id=request_id,
             extra={"numero": facture.numero})
    return Facture.objects.get(pk=facture.pk)

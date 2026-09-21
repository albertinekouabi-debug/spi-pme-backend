"""
Point d'entrée unique pour journaliser une action sensible. Les autres
modules l'appellent via un import différé (`from apps.audit.services import
enregistrer`) pour éviter tout couplage au chargement des apps — le module
audit ne doit dépendre de rien d'autre que accounts.Utilisateur.
"""
from .models import JournalAudit


def enregistrer(action: str, module: str, resultat: str = "reussi", auteur=None, cible=None, details=None, adresse_ip=None) -> JournalAudit:
    return JournalAudit.objects.create(
        action=action,
        module=module,
        cible_type=cible.__class__.__name__ if cible is not None else "",
        cible_id=str(cible.pk) if cible is not None else "",
        resultat=resultat,
        details=details or {},
        adresse_ip=adresse_ip,
        auteur=auteur,
    )

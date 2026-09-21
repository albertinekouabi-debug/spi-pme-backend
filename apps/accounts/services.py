"""
apps.accounts.services — envoi d'email pour l'auto-inscription (décision
produit du 16/09/2026 : vérification email obligatoire avant activation).

IMPORTANT — état réel de la configuration email (vérifié, pas supposé) :
aucun service d'envoi n'était configuré dans ce projet avant ce correctif.
EMAIL_BACKEND pointe par défaut vers le backend "console" de Django
(affiche l'email dans les logs du serveur au lieu de l'envoyer réellement) —
suffisant pour développer et tester le flux de bout en bout, mais PAS
utilisable en production tel quel. Voir settings.py : la variable
d'environnement DJANGO_EMAIL_BACKEND permet de basculer vers un vrai
backend SMTP en production, une fois les identifiants du fournisseur
choisi disponibles (décision produit restant à prendre).
"""
from django.conf import settings
from django.core.mail import send_mail


def envoyer_email_verification(utilisateur, token_brut: str) -> None:
    lien = f"{settings.URL_FRONTEND_VERIFICATION}?token={token_brut}"
    send_mail(
        subject="Confirmez votre inscription — SPI-PME",
        message=(
            f"Bonjour {utilisateur.nom_utilisateur},\n\n"
            "Merci de vous être inscrit sur SPI-PME. Cliquez sur le lien "
            f"ci-dessous pour activer votre compte (valide 24 heures) :\n\n"
            f"{lien}\n\n"
            "Si vous n'êtes pas à l'origine de cette inscription, vous "
            "pouvez ignorer cet email sans risque."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[utilisateur.email],
        fail_silently=False,
    )

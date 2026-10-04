"""
Périmètre sectoriel (§3.2) — source unique pour la validation à l'ÉCRITURE.

Les lectures sont déjà filtrées par les `get_queryset` des vues. Ce module
ferme le côté écriture (audit BE-005) : un utilisateur ne peut ni écrire dans
un secteur hors périmètre, ni lier un objet d'un autre secteur au sien.
"""
from rest_framework import serializers


def secteurs_autorises(user):
    """Ensemble des ids de secteurs accessibles ; None = aucune restriction (Administrateur)."""
    if user.role and user.role.nom == "Administrateur":
        return None
    ids = set(user.secteurs.values_list("id", flat=True))
    if user.secteur_principal_id:
        ids.add(user.secteur_principal_id)
    return ids


def verifier_perimetre(serializer, attrs, liens=()):
    """
    À appeler au début de `validate()` d'un serializer portant un champ `secteur`.

    `liens` : noms des clés étrangères qui doivent appartenir au même secteur
    que l'objet (ex. ("entite", "ressource")). Elles sont contrôlées lorsqu'elles
    sont fournies, ou toutes si le secteur change (pas de blocage sur des
    données historiques lors d'une mise à jour sans rapport).
    """
    request = serializer.context.get("request")
    if request is None or not request.user.is_authenticated:
        return  # usage interne (services, commandes) : pas de contexte HTTP

    instance = serializer.instance
    secteur = attrs.get("secteur", getattr(instance, "secteur", None))
    autorises = secteurs_autorises(request.user)

    if "secteur" in attrs and secteur is not None and autorises is not None and secteur.id not in autorises:
        raise serializers.ValidationError({"secteur": "Ce secteur ne fait pas partie de votre périmètre."})

    if secteur is None:
        return
    for champ in liens:
        if champ not in attrs and "secteur" not in attrs:
            continue
        objet = attrs.get(champ, getattr(instance, champ, None))
        if objet is not None and getattr(objet, "secteur_id", secteur.id) != secteur.id:
            raise serializers.ValidationError(
                {champ: "Cet élément appartient à un autre secteur que l'objet modifié."}
            )

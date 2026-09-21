"""
Validation d'une ligne importée. Retourne (donnees_normalisees, erreur) où
`erreur` est None si la ligne est valide, sinon {"colonne": ..., "motif": ...}
— une seule erreur par ligne (la première rencontrée), pour rester lisible ;
une ligne avec plusieurs problèmes sera de nouveau signalée après correction
et ré-import.

Note de portée : la maquette montre "Catégorie invalide : la catégorie
'Boissons' n'existe pas", ce qui suppose une liste de catégories autorisées
par secteur. Cette liste n'existe dans aucune table du modèle de données
(ConfigurationSectorielle.libelles ne fait que renommer l'affichage, ce
n'est pas une liste blanche). Je valide donc seulement la présence de la
catégorie, pas son appartenance à une liste — pour valider l'appartenance,
il faudrait d'abord définir où cette liste est censée être configurée.
"""
from decimal import Decimal, InvalidOperation
from typing import Optional


def _vers_decimal(valeur) -> Optional[Decimal]:
    if valeur is None:
        return None
    texte = str(valeur).strip().replace(",", ".").replace(" ", "")
    if texte == "":
        return None
    try:
        return Decimal(texte)
    except InvalidOperation:
        return None


def valider_ligne(ligne: dict) -> tuple[Optional[dict], Optional[dict]]:
    nom = str(ligne.get("nom", "")).strip()
    if not nom:
        return None, {"colonne": "Nom", "motif": "Nom manquant"}

    categorie = str(ligne.get("categorie", "")).strip()
    if not categorie:
        return None, {"colonne": "Catégorie", "motif": "Catégorie manquante"}

    quantite_brute = ligne.get("quantite")
    if quantite_brute is None or str(quantite_brute).strip() == "":
        return None, {"colonne": "Quantité", "motif": "Quantité manquante"}
    quantite = _vers_decimal(quantite_brute)
    if quantite is None:
        return None, {"colonne": "Quantité", "motif": f"Valeur '{quantite_brute}' invalide"}
    if quantite < 0:
        return None, {"colonne": "Quantité", "motif": "La quantité ne peut pas être négative"}

    valeur_unitaire = None
    valeur_unitaire_brute = ligne.get("valeur_unitaire")
    if valeur_unitaire_brute is not None and str(valeur_unitaire_brute).strip() != "":
        valeur_unitaire = _vers_decimal(valeur_unitaire_brute)
        if valeur_unitaire is None:
            return None, {"colonne": "Valeur unitaire", "motif": f"Valeur '{valeur_unitaire_brute}' invalide"}

    seuil_critique = None
    seuil_critique_brut = ligne.get("seuil_critique")
    if seuil_critique_brut is not None and str(seuil_critique_brut).strip() != "":
        seuil_critique = _vers_decimal(seuil_critique_brut)
        if seuil_critique is None:
            return None, {"colonne": "Seuil critique", "motif": f"Valeur '{seuil_critique_brut}' invalide"}

    seuil_alerte = None
    seuil_alerte_brut = ligne.get("seuil_alerte")
    if seuil_alerte_brut is not None and str(seuil_alerte_brut).strip() != "":
        seuil_alerte = _vers_decimal(seuil_alerte_brut)
        if seuil_alerte is None:
            return None, {"colonne": "Seuil d'alerte", "motif": f"Valeur '{seuil_alerte_brut}' invalide"}

    if seuil_critique is not None and seuil_alerte is not None and seuil_critique > seuil_alerte:
        return None, {"colonne": "Seuil critique", "motif": "Le seuil critique doit être inférieur ou égal au seuil d'alerte"}

    return {
        "nom": nom,
        "categorie": categorie,
        "quantite": quantite,
        "unite": str(ligne.get("unite", "")).strip(),
        "emplacement": str(ligne.get("emplacement", "")).strip(),
        "valeur_unitaire": valeur_unitaire,
        "seuil_critique": seuil_critique,
        "seuil_alerte": seuil_alerte,
    }, None

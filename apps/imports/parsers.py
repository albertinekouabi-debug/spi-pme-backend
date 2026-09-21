"""
Lecture de fichiers CSV/XLSX pour l'import de ressources.
Retourne toujours une liste de dicts avec des clés canoniques, quelle que
soit l'orthographe exacte des en-têtes du fichier source (accents, casse).
"""
import csv
import io
import unicodedata

from openpyxl import load_workbook

ENTETES_CANONIQUES = {
    "nom": "nom",
    "categorie": "categorie",
    "quantite": "quantite",
    "unite": "unite",
    "entrepot": "emplacement",
    "valeur unitaire": "valeur_unitaire",
    "seuil critique": "seuil_critique",
    "seuil d'alerte": "seuil_alerte",
    "seuil alerte": "seuil_alerte",
}


def _normaliser_entete(entete: str) -> str:
    """'Valeur unitaire' -> 'valeur unitaire' ; 'Catégorie' -> 'categorie' ; 'Entrepôt' -> 'entrepot'."""
    sans_accents = unicodedata.normalize("NFKD", entete.strip().lower()).encode("ascii", "ignore").decode()
    return ENTETES_CANONIQUES.get(sans_accents, sans_accents)


def parser_csv(contenu: bytes) -> list[dict]:
    texte = contenu.decode("utf-8-sig")  # gère le BOM Excel
    lecteur = csv.DictReader(io.StringIO(texte))
    entetes = {brut: _normaliser_entete(brut) for brut in (lecteur.fieldnames or [])}
    return [{entetes.get(cle, cle): valeur for cle, valeur in ligne.items()} for ligne in lecteur]


def parser_xlsx(contenu: bytes) -> list[dict]:
    classeur = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    feuille = classeur.active
    lignes_brutes = feuille.iter_rows(values_only=True)

    try:
        entetes_brutes = next(lignes_brutes)
    except StopIteration:
        return []
    entetes = [_normaliser_entete(str(h)) if h is not None else f"colonne_{i}" for i, h in enumerate(entetes_brutes)]

    resultats = []
    for ligne in lignes_brutes:
        if ligne is None or all(v is None for v in ligne):
            continue  # ignore les lignes entièrement vides en fin de feuille
        resultats.append({
            entetes[i]: ("" if valeur is None else valeur)
            for i, valeur in enumerate(ligne) if i < len(entetes)
        })
    return resultats


def parser_fichier(nom_fichier: str, contenu: bytes) -> list[dict]:
    extension = nom_fichier.rsplit(".", 1)[-1].lower() if "." in nom_fichier else ""
    if extension == "csv":
        return parser_csv(contenu)
    if extension in ("xlsx", "xls"):
        return parser_xlsx(contenu)
    raise ValueError(f"Format de fichier non supporté : .{extension} (attendu : .csv ou .xlsx)")

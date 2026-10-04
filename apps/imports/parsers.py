"""
Lecture de fichiers CSV/XLSX pour l'import de ressources.
Retourne toujours une liste de dicts avec des clés canoniques, quelle que
soit l'orthographe exacte des en-têtes du fichier source (accents, casse).
"""
import csv
import io
import zipfile
import unicodedata

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

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


MAX_LIGNES = 10_000                       # borne mémoire/CPU d'un import (audit BE-006)
MAX_TAILLE_DECOMPRESSEE = 50 * 1024 * 1024  # protège des « zip bombs » dans un .xlsx


def _decoder_csv(contenu: bytes) -> str:
    if b"\x00" in contenu:
        raise ValueError("Fichier CSV illisible : contenu binaire détecté.")
    try:
        return contenu.decode("utf-8-sig")  # gère le BOM Excel
    except UnicodeDecodeError:
        # CSV exporté par Excel sous Windows (français) : Windows-1252.
        return contenu.decode("cp1252", errors="replace")


def _delimiteur(texte: str) -> str:
    premiere_ligne = texte.split("\n", 1)[0]
    return max((",", ";", "\t"), key=premiere_ligne.count)


def parser_csv(contenu: bytes) -> list[dict]:
    texte = _decoder_csv(contenu)
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=_delimiteur(texte))
    entetes = {brut: _normaliser_entete(brut) for brut in (lecteur.fieldnames or [])}
    lignes = []
    for ligne in lecteur:
        if len(lignes) >= MAX_LIGNES:
            raise ValueError(f"Fichier trop volumineux : {MAX_LIGNES} lignes maximum par import.")
        lignes.append({entetes.get(cle, cle): valeur for cle, valeur in ligne.items()})
    return lignes


def parser_xlsx(contenu: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_TAILLE_DECOMPRESSEE:
                raise ValueError("Classeur refusé : taille décompressée excessive.")
        classeur = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    except ValueError:
        raise
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError) as exc:
        raise ValueError("Fichier .xlsx illisible ou corrompu.") from exc
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
        if len(resultats) >= MAX_LIGNES:
            raise ValueError(f"Fichier trop volumineux : {MAX_LIGNES} lignes maximum par import.")
        resultats.append({
            entetes[i]: ("" if valeur is None else valeur)
            for i, valeur in enumerate(ligne) if i < len(entetes)
        })
    return resultats


def parser_fichier(nom_fichier: str, contenu: bytes) -> list[dict]:
    extension = nom_fichier.rsplit(".", 1)[-1].lower() if "." in nom_fichier else ""
    if extension == "csv":
        return parser_csv(contenu)
    if extension == "xlsx":
        return parser_xlsx(contenu)
    raise ValueError(f"Format de fichier non supporté : .{extension} (attendu : .csv ou .xlsx)")

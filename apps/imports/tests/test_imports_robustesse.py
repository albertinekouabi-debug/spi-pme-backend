"""Robustesse du module d'import face à des fichiers hostiles ou mal formés (audit BE-006)."""
import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import Secteur
from apps.imports.parsers import MAX_LIGNES, parser_csv, parser_fichier
from apps.imports.serializers import TAILLE_MAX_OCTETS

ENTETES = "nom;categorie;quantite\n"


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def client_gerant(db, secteur):
    role = Role.objects.create(nom="Gérant")
    for code in ["imports.read", "imports.write"]:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"module": "imports"})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    user = Utilisateur.objects.create_user(
        email="g@spipme.com", nom_utilisateur="g1", password="MotDePasse#2026",
        role=role, secteur_principal=secteur,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _envoyer(client, secteur, nom, contenu, action="preview"):
    return client.post(
        f"/api/v1/imports/{action}/",
        {"fichier": SimpleUploadedFile(nom, contenu), "secteur": secteur.id},
        format="multipart",
    )


class TestCsvTolerant:
    def test_delimiteur_point_virgule_excel_fr(self):
        lignes = parser_csv((ENTETES + "Riz;Alimentaire;10\n").encode("utf-8"))
        assert lignes == [{"nom": "Riz", "categorie": "Alimentaire", "quantite": "10"}]

    def test_encodage_windows_1252_accepte(self):
        lignes = parser_csv((ENTETES + "Café;Boisson;3\n").encode("cp1252"))
        assert lignes[0]["nom"] == "Café"

    def test_binaire_illisible_donne_une_erreur_metier(self):
        with pytest.raises(ValueError):
            parser_csv(b"\x00\x01\x02\xff\xfe")


class TestXlsxHostile:
    def test_fichier_zip_corrompu_donne_une_erreur_metier(self):
        with pytest.raises(ValueError):
            parser_fichier("x.xlsx", b"ceci n'est pas un zip")

    def test_zip_valide_mais_pas_un_classeur(self):
        tampon = io.BytesIO()
        with zipfile.ZipFile(tampon, "w") as z:
            z.writestr("a.txt", "x")
        with pytest.raises(ValueError):
            parser_fichier("x.xlsx", tampon.getvalue())

    def test_trop_de_lignes_refuse(self):
        contenu = ENTETES + "".join(f"n{i};c;1\n" for i in range(MAX_LIGNES + 1))
        with pytest.raises(ValueError, match="lignes"):
            parser_fichier("x.csv", contenu.encode("utf-8"))


@pytest.mark.django_db
class TestAPIImport:
    def test_fichier_trop_volumineux_refuse_400(self, client_gerant, secteur):
        r = _envoyer(client_gerant, secteur, "gros.csv", b"a" * (TAILLE_MAX_OCTETS + 1))
        assert r.status_code == 400

    def test_xls_ancien_format_refuse_400_explicite(self, client_gerant, secteur):
        r = _envoyer(client_gerant, secteur, "vieux.xls", b"\xd0\xcf\x11\xe0")
        assert r.status_code == 400
        assert "xlsx" in str(r.data).lower()

    def test_xlsx_corrompu_renvoie_400_pas_500(self, client_gerant, secteur):
        r = _envoyer(client_gerant, secteur, "casse.xlsx", b"pas un zip")
        assert r.status_code == 400

    def test_csv_binaire_renvoie_400_pas_500(self, client_gerant, secteur):
        r = _envoyer(client_gerant, secteur, "bin.csv", b"\x00\x01\xff\xfe\x00")
        assert r.status_code == 400

    def test_debit_limite_applique_a_la_vue(self):
        from apps.imports.views import ImportFichierViewSet
        assert ImportFichierViewSet.throttle_scope == "imports"

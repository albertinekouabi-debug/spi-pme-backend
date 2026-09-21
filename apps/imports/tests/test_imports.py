import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import Secteur
from apps.imports import services
from apps.imports.models import ImportFichier
from apps.imports.parsers import parser_csv, parser_xlsx
from apps.imports.validators import valider_ligne
from apps.resources.models import Ressource


def _csv_bytes(entetes, lignes):
    contenu = ",".join(entetes) + "\n"
    for ligne in lignes:
        contenu += ",".join(str(v) for v in ligne) + "\n"
    return contenu.encode("utf-8")


def _xlsx_bytes(entetes, lignes):
    classeur = Workbook()
    feuille = classeur.active
    feuille.append(entetes)
    for ligne in lignes:
        feuille.append(ligne)
    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def role_gerant(db):
    role = Role.objects.create(nom="Gérant")
    for code in ["imports.read", "imports.write"]:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"module": "imports"})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    return role


@pytest.fixture
def gerant(db, role_gerant, secteur):
    return Utilisateur.objects.create_user(
        email="gerant@spipme.com", nom_utilisateur="gerant1",
        password="MotDePasse#2026", role=role_gerant, secteur_principal=secteur,
    )


class TestParsers:
    def test_parse_csv_normalise_les_entetes_accentuees(self):
        contenu = _csv_bytes(
            ["Nom", "Catégorie", "Quantité", "Unité", "Entrepôt", "Valeur unitaire"],
            [["Lait en poudre", "Produits laitiers", "120", "Unités", "Entrepôt principal", "5500"]],
        )
        lignes = parser_csv(contenu)
        assert lignes == [{
            "nom": "Lait en poudre", "categorie": "Produits laitiers", "quantite": "120",
            "unite": "Unités", "emplacement": "Entrepôt principal", "valeur_unitaire": "5500",
        }]

    def test_parse_xlsx(self):
        contenu = _xlsx_bytes(
            ["Nom", "Catégorie", "Quantité"],
            [["Riz étuvé 25kg", "Céréales", 18]],
        )
        lignes = parser_xlsx(contenu)
        assert lignes[0]["nom"] == "Riz étuvé 25kg"
        assert lignes[0]["quantite"] == 18

    def test_lignes_vides_xlsx_ignorees(self):
        contenu = _xlsx_bytes(["Nom", "Quantite"], [["A", 1], [None, None], ["B", 2]])
        lignes = parser_xlsx(contenu)
        assert len(lignes) == 2


class TestValidateurs:
    def test_ligne_valide(self):
        donnees, erreur = valider_ligne({"nom": "Huile 5L", "categorie": "Huiles", "quantite": "120"})
        assert erreur is None
        assert donnees["quantite"] == 120

    def test_quantite_manquante(self):
        _, erreur = valider_ligne({"nom": "X", "categorie": "Y", "quantite": ""})
        assert erreur == {"colonne": "Quantité", "motif": "Quantité manquante"}

    def test_quantite_invalide(self):
        _, erreur = valider_ligne({"nom": "X", "categorie": "Y", "quantite": "quatre cent"})
        assert erreur == {"colonne": "Quantité", "motif": "Valeur 'quatre cent' invalide"}

    def test_categorie_manquante(self):
        _, erreur = valider_ligne({"nom": "X", "categorie": "", "quantite": "5"})
        assert erreur["colonne"] == "Catégorie"

    def test_valeur_unitaire_invalide(self):
        _, erreur = valider_ligne({"nom": "X", "categorie": "Y", "quantite": "5", "valeur_unitaire": "abc"})
        assert erreur == {"colonne": "Valeur unitaire", "motif": "Valeur 'abc' invalide"}

    def test_valeur_unitaire_absente_est_acceptee(self):
        donnees, erreur = valider_ligne({"nom": "X", "categorie": "Y", "quantite": "5"})
        assert erreur is None
        assert donnees["valeur_unitaire"] is None


@pytest.mark.django_db
class TestServicePrevisualiser:
    def test_apercu_ne_persiste_rien(self, secteur):
        contenu = _csv_bytes(["Nom", "Categorie", "Quantite"], [["Sucre 50kg", "Épicerie", "85"]])
        resultat = services.previsualiser("stock.csv", contenu)
        assert resultat["lignes_valides"] == 1
        assert Ressource.objects.count() == 0
        assert ImportFichier.objects.count() == 0

    def test_apercu_rapporte_les_erreurs(self, secteur):
        contenu = _csv_bytes(
            ["Nom", "Categorie", "Quantite"],
            [["A", "Cat", "10"], ["B", "Cat", ""], ["C", "Cat", "abc"]],
        )
        resultat = services.previsualiser("stock.csv", contenu)
        assert resultat["lignes_valides"] == 1
        assert resultat["lignes_rejetees"] == 2
        assert len(resultat["rapport_anomalies"]) == 2


@pytest.mark.django_db
class TestServiceImporter:
    def test_import_partiel_cree_les_ressources_valides(self, secteur):
        contenu = _csv_bytes(
            ["Nom", "Categorie", "Quantite", "Valeur unitaire"],
            [["Lait en poudre", "Produits laitiers", "120", "5500"], ["Riz", "Céréales", ""]],
        )
        import_fichier = services.importer("stock.csv", contenu, secteur, auteur=None)

        assert import_fichier.statut == "termine_avec_anomalies"
        assert import_fichier.lignes_importees == 1
        assert import_fichier.lignes_rejetees == 1
        assert Ressource.objects.count() == 1

        ressource = Ressource.objects.get(nom="Lait en poudre")
        assert ressource.niveau_actuel == 120
        assert ressource.valeur_unitaire == 5500
        assert ressource.statut == "stable"  # pas de seuils configurés -> stable par défaut, pas inventé

    def test_upsert_met_a_jour_une_ressource_existante_sans_ecraser_ses_seuils(self, secteur):
        Ressource.objects.create(
            type="produit", nom="Huile 5L", secteur=secteur,
            niveau_actuel=10, seuil_critique=5, seuil_alerte=20,
        )
        contenu = _csv_bytes(["Nom", "Categorie", "Quantite"], [["Huile 5L", "Huiles", "3"]])
        services.importer("maj.csv", contenu, secteur, auteur=None)

        ressource = Ressource.objects.get(nom="Huile 5L")
        assert ressource.niveau_actuel == 3
        assert ressource.seuil_critique == 5  # conservé
        assert ressource.statut == "critique"  # recalculé avec les seuils existants

    def test_echec_total_si_toutes_les_lignes_sont_invalides(self, secteur):
        contenu = _csv_bytes(["Nom", "Categorie", "Quantite"], [["A", "Cat", ""]])
        import_fichier = services.importer("mauvais.csv", contenu, secteur, auteur=None)
        assert import_fichier.statut == "echec"
        assert import_fichier.lignes_importees == 0

    def test_format_non_supporte_leve_une_erreur(self, secteur):
        with pytest.raises(ValueError):
            services.importer("stock.txt", b"peu importe", secteur, auteur=None)


    def test_import_avec_colonnes_de_seuils(self, secteur):
        contenu = _csv_bytes(
            ["Nom", "Categorie", "Quantite", "Seuil critique", "Seuil d'alerte"],
            [["Lait en poudre", "Produits laitiers", "120", "20", "50"]],
        )
        import_fichier = services.importer("stock.csv", contenu, secteur, auteur=None)
        assert import_fichier.statut == "termine"
        ressource = Ressource.objects.get(nom="Lait en poudre")
        assert ressource.seuil_critique == 20
        assert ressource.seuil_alerte == 50
        assert ressource.statut == "stable"  # 120 > 50

    def test_seuils_absents_de_limport_laissent_la_ressource_sans_seuils(self, secteur):
        contenu = _csv_bytes(["Nom", "Categorie", "Quantite"], [["Sucre 50kg", "Épicerie", "85"]])
        services.importer("stock.csv", contenu, secteur, auteur=None)
        ressource = Ressource.objects.get(nom="Sucre 50kg")
        assert ressource.seuil_critique is None

    def test_seuil_critique_superieur_au_seuil_alerte_est_rejete(self, secteur):
        contenu = _csv_bytes(
            ["Nom", "Categorie", "Quantite", "Seuil critique", "Seuil d'alerte"],
            [["X", "Cat", "10", "50", "20"]],
        )
        import_fichier = services.importer("stock.csv", contenu, secteur, auteur=None)
        assert import_fichier.statut == "echec"
        assert import_fichier.rapport_anomalies[0]["colonne"] == "Seuil critique"


@pytest.mark.django_db
class TestAPIImport:
    def test_preview_via_api_ne_persiste_rien(self, gerant, secteur):
        contenu = _csv_bytes(["Nom", "Categorie", "Quantite"], [["Sucre 50kg", "Épicerie", "85"]])
        fichier = SimpleUploadedFile("stock.csv", contenu, content_type="text/csv")
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(
            "/api/v1/imports/preview/", {"fichier": fichier, "secteur": secteur.id}, format="multipart"
        )
        assert response.status_code == 200
        assert response.data["lignes_valides"] == 1
        assert Ressource.objects.count() == 0

    def test_commit_via_api_persiste(self, gerant, secteur):
        contenu = _csv_bytes(["Nom", "Categorie", "Quantite"], [["Sucre 50kg", "Épicerie", "85"]])
        fichier = SimpleUploadedFile("stock.csv", contenu, content_type="text/csv")
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(
            "/api/v1/imports/commit/", {"fichier": fichier, "secteur": secteur.id}, format="multipart"
        )
        assert response.status_code == 201
        assert response.data["statut"] == "termine"
        assert Ressource.objects.count() == 1

    def test_format_non_supporte_renvoie_400(self, gerant, secteur):
        fichier = SimpleUploadedFile("stock.txt", b"peu importe", content_type="text/plain")
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(
            "/api/v1/imports/preview/", {"fichier": fichier, "secteur": secteur.id}, format="multipart"
        )
        assert response.status_code == 400

    def test_employe_sans_permission_est_rejete(self, secteur):
        role_employe = Role.objects.create(nom="Employé")
        employe = Utilisateur.objects.create_user(
            email="employe@spipme.com", nom_utilisateur="employe1",
            password="MotDePasse#2026", role=role_employe, secteur_principal=secteur,
        )
        client = APIClient()
        client.force_authenticate(user=employe)
        response = client.get("/api/v1/imports/")
        assert response.status_code == 403

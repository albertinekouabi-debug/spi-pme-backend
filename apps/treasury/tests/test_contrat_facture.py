"""
Contrat JSON de /invoices pour le client Android (audit AN-003).

La TVA est optionnelle : sans taux, `taux_tva` ET `montant_tva` valent `null`.
Le DTO Android doit donc les déclarer nullables ; ce test empêche le serveur
de changer ce contrat sans que le client soit adapté.
"""
import json

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import Secteur
from apps.registry.models import Entite
from apps.treasury.models import Facture


@pytest.fixture
def contexte(db):
    secteur = Secteur.objects.create(code="commerce", nom="Commerce")
    role = Role.objects.create(nom="Comptable")
    perm, _ = Permission.objects.get_or_create(code="treasury.read", defaults={"module": "treasury"})
    RolePermission.objects.get_or_create(role=role, permission=perm)
    user = Utilisateur.objects.create_user(
        email="c@spipme.com", nom_utilisateur="compta", password="MotDePasse#2026",
        role=role, secteur_principal=secteur,
    )
    entite = Entite.objects.create(type="client", nom="Client", secteur=secteur)
    client = APIClient()
    client.force_authenticate(user=user)
    return client, secteur, entite


def _facture_json(client):
    reponse = client.get("/api/v1/invoices/")
    assert reponse.status_code == 200
    return json.loads(reponse.content)["results"][0]  # JSON brut, pas response.data


def test_facture_sans_tva_expose_null_pour_taux_et_montant_tva(contexte):
    client, secteur, entite = contexte
    Facture.objects.create(numero="F-1", entite=entite, montant="1000.00", secteur=secteur)
    f = _facture_json(client)
    assert f["taux_tva"] is None
    assert f["montant_tva"] is None
    assert f["montant_ttc"] == "1000.00"  # TTC = HT quand aucune TVA n'est saisie


def test_facture_avec_tva_expose_des_chaines_decimales(contexte):
    client, secteur, entite = contexte
    Facture.objects.create(numero="F-2", entite=entite, montant="1000.00", taux_tva="18.00", secteur=secteur)
    f = _facture_json(client)
    assert f["taux_tva"] == "18.00"
    assert f["montant_tva"] == "180.00"
    assert f["montant_ttc"] == "1180.00"

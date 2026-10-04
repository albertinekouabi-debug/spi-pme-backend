"""Garde-fou de performance : le nombre de requêtes SQL ne doit pas croître avec le volume de données."""
import datetime as dt
import time

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import Secteur
from apps.registry.models import Entite
from apps.treasury.models import Facture, Transaction


def _scenario(n_tx, n_factures):
    s = Secteur.objects.create(code="commerce", nom="Commerce")
    r = Role.objects.create(nom="Dir")
    p, _ = Permission.objects.get_or_create(code="treasury.read", defaults={"module": "treasury"})
    RolePermission.objects.create(role=r, permission=p)
    u = Utilisateur.objects.create_user(email="d@x.com", nom_utilisateur="d", password="MotDePasse#2026", role=r, secteur_principal=s)
    entites = [Entite.objects.create(type="client", nom=f"C{i}", secteur=s) for i in range(20)]
    maintenant = timezone.now()
    Transaction.objects.bulk_create([
        Transaction(type="entree" if i % 2 else "sortie", montant="100.00", secteur=s, statut="validee",
                    entite=entites[i % 20], date_transaction=maintenant - dt.timedelta(days=i % 180)) for i in range(n_tx)])
    Facture.objects.bulk_create([
        Facture(numero=f"F{i}", entite=entites[i % 20], montant="500.00", secteur=s, statut="impayee",
                date_echeance=timezone.now().date() - dt.timedelta(days=5)) for i in range(n_factures)])
    c = APIClient(); c.force_authenticate(user=u)
    return c


def _requetes(c, url):
    with CaptureQueriesContext(connection) as q:
        t0 = time.perf_counter()
        r = c.get(url)
        duree = time.perf_counter() - t0
    assert r.status_code == 200
    return len(q), duree


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/api/v1/analytics/kpis", "/api/v1/analytics/insights", "/api/v1/analytics/anomalies",
                                 "/api/v1/analytics/forecast/tresorerie"])
def test_le_nombre_de_requetes_ne_depend_pas_du_volume(url):
    c = _scenario(40, 5)
    petit = _requetes(c, url)[0]
    Transaction.objects.bulk_create([
        Transaction(type="entree", montant="10.00", secteur=Secteur.objects.get(), statut="validee",
                    date_transaction=timezone.now() - dt.timedelta(days=i % 150)) for i in range(3000)])
    Facture.objects.bulk_create([
        Facture(numero=f"G{i}", entite=Entite.objects.first(), montant="5.00", secteur=Secteur.objects.get(),
                statut="impayee", date_echeance=timezone.now().date() - dt.timedelta(days=3)) for i in range(400)])
    gros = _requetes(c, url)[0]
    assert gros == petit, f"N+1 : {petit} requêtes → {gros} avec 75× plus de données"


@pytest.mark.django_db
def test_insights_sur_2000_transactions_et_250_factures_reste_rapide_et_borne():
    c = _scenario(2000, 250)
    nb, duree = _requetes(c, "/api/v1/analytics/insights")
    print(f"\n[mesure] insights : {nb} requêtes SQL, {duree * 1000:.0f} ms (2000 transactions, 250 factures)")
    assert nb <= 45 and duree < 2.0

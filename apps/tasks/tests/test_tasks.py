from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Permission, Role, RolePermission, Utilisateur
from apps.core.models import Secteur
from apps.tasks.models import Tache, TacheHistoriqueStatut


@pytest.fixture
def secteur(db):
    return Secteur.objects.create(code="commerce", nom="Commerce")


@pytest.fixture
def role_gerant(db):
    role = Role.objects.create(nom="Gérant")
    for code in ["tasks.read", "tasks.write"]:
        perm, _ = Permission.objects.get_or_create(code=code, defaults={"module": "tasks"})
        RolePermission.objects.get_or_create(role=role, permission=perm)
    return role


@pytest.fixture
def gerant(db, role_gerant, secteur):
    return Utilisateur.objects.create_user(
        email="gerant@spipme.com", nom_utilisateur="gerant1",
        password="MotDePasse#2026", role=role_gerant, secteur_principal=secteur,
    )


@pytest.mark.django_db
class TestModeleTache:
    def test_creation_trace_lhistorique_initial(self, secteur):
        t = Tache.objects.create(titre="Vérifier les niveaux de stock", statut="a_faire", secteur=secteur)
        historique = TacheHistoriqueStatut.objects.filter(tache=t)
        assert historique.count() == 1
        assert historique.first().nouveau_statut == "a_faire"
        assert historique.first().ancien_statut is None

    def test_changement_de_statut_est_journalise(self, secteur):
        t = Tache.objects.create(titre="Préparer rapport mensuel", statut="a_faire", secteur=secteur)
        t.statut = "en_cours"
        t.save()
        t.statut = "terminee"
        t.save()
        historique = list(TacheHistoriqueStatut.objects.filter(tache=t).order_by("date_changement"))
        assert len(historique) == 3
        assert [h.nouveau_statut for h in historique] == ["a_faire", "en_cours", "terminee"]
        assert historique[1].ancien_statut == "a_faire"
        assert historique[2].ancien_statut == "en_cours"

    def test_pas_de_doublon_si_le_statut_ne_change_pas(self, secteur):
        t = Tache.objects.create(titre="Mettre à jour la liste des fournisseurs", statut="a_faire", secteur=secteur)
        t.titre = "Mettre à jour la liste des fournisseurs (v2)"
        t.save()
        assert TacheHistoriqueStatut.objects.filter(tache=t).count() == 1

    def test_en_retard_si_echeance_depassee_et_non_terminee(self, secteur):
        hier = timezone.now().date() - timedelta(days=1)
        t = Tache.objects.create(titre="Commander du stock critique", statut="a_faire", echeance=hier, secteur=secteur)
        assert t.en_retard is True

    def test_pas_en_retard_si_terminee_meme_en_retard_dechance(self, secteur):
        hier = timezone.now().date() - timedelta(days=1)
        t = Tache.objects.create(titre="Suivre les paiements clients", statut="terminee", echeance=hier, secteur=secteur)
        assert t.en_retard is False


@pytest.mark.django_db
class TestAPITache:
    def test_creation_associe_le_createur(self, gerant, secteur):
        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.post(
            "/api/v1/tasks/",
            {"titre": "Planifier campagne marketing", "secteur": secteur.id, "priorite": "moyenne"},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["createur"] == gerant.id

    def test_historique_via_api_trace_lauteur_du_changement(self, gerant, secteur):
        client = APIClient()
        client.force_authenticate(user=gerant)
        creation = client.post(
            "/api/v1/tasks/", {"titre": "Vérifier les niveaux de stock", "secteur": secteur.id}, format="json"
        )
        tache_id = creation.data["id"]
        client.patch(f"/api/v1/tasks/{tache_id}/", {"statut": "terminee"}, format="json")

        response = client.get(f"/api/v1/tasks/{tache_id}/history/")
        assert response.status_code == 200
        assert len(response.data) == 2
        assert response.data[0]["nouveau_statut"] == "terminee"
        assert response.data[0]["auteur"] == gerant.id

    def test_resume(self, gerant, secteur):
        hier = timezone.now().date() - timedelta(days=1)
        demain = timezone.now().date() + timedelta(days=1)
        Tache.objects.create(titre="A", statut="terminee", secteur=secteur)
        Tache.objects.create(titre="B", statut="en_cours", echeance=demain, secteur=secteur)
        Tache.objects.create(titre="C", statut="a_faire", echeance=hier, secteur=secteur)  # en retard

        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get("/api/v1/tasks/summary/")
        assert response.status_code == 200
        assert response.data == {"total": 3, "terminees": 1, "en_cours": 1, "en_retard": 1}

    def test_filtre_retard(self, gerant, secteur):
        hier = timezone.now().date() - timedelta(days=1)
        Tache.objects.create(titre="En retard", statut="a_faire", echeance=hier, secteur=secteur)
        Tache.objects.create(titre="À jour", statut="a_faire", secteur=secteur)

        client = APIClient()
        client.force_authenticate(user=gerant)
        response = client.get("/api/v1/tasks/", {"retard": "true"})
        assert response.data["count"] == 1
        assert response.data["results"][0]["titre"] == "En retard"

    def test_employe_sans_permission_est_rejete(self, secteur):
        role_employe = Role.objects.create(nom="Employé")
        employe = Utilisateur.objects.create_user(
            email="employe@spipme.com", nom_utilisateur="employe1",
            password="MotDePasse#2026", role=role_employe, secteur_principal=secteur,
        )
        client = APIClient()
        client.force_authenticate(user=employe)
        response = client.get("/api/v1/tasks/")
        assert response.status_code == 403

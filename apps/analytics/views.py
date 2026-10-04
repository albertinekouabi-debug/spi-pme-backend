from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasRolePermission
from apps.audit.services import enregistrer
from apps.core.models import Secteur
from apps.core.perimetre import secteurs_autorises

from . import anomalies as moteur_anomalies
from . import copilote, insights, kpi, series
from .previsions import prevoir


def _secteur(request):
    """?secteur=<id> (ou secteur_principal). Refusé hors du périmètre de l'utilisateur."""
    corps = request.data if hasattr(request.data, "get") else {}
    # Un secteur explicitement demandé (URL ou corps) n'est JAMAIS remplacé silencieusement par le secteur principal.
    secteur_id = request.query_params.get("secteur") or corps.get("secteur") or request.user.secteur_principal_id
    if not secteur_id:
        raise ValidationError({"secteur": "Secteur requis."})
    try:
        secteur_id = int(secteur_id)
    except (TypeError, ValueError):
        raise ValidationError({"secteur": "Identifiant de secteur invalide."})
    autorises = secteurs_autorises(request.user)
    if autorises is not None and secteur_id not in autorises:
        raise PermissionDenied("Ce secteur ne fait pas partie de votre périmètre.")
    try:
        return Secteur.objects.get(pk=secteur_id)
    except Secteur.DoesNotExist:
        raise ValidationError({"secteur": "Secteur introuvable."})


class _AnalyticsBase(APIView):
    permission_classes = [HasRolePermission]
    required_permissions = {"GET": ["treasury.read"], "POST": ["treasury.read"]}
    throttle_scope = "analytics"


class KpiView(_AnalyticsBase):
    def get(self, request):
        return Response(kpi.calculer_kpis(_secteur(request), timezone.now().date()))


class InsightsView(_AnalyticsBase):
    def get(self, request):
        liste, _, _ = insights.generer(_secteur(request), timezone.now().date())
        return Response({"insights": liste, "version_moteur": insights.VERSION_MOTEUR})


class AnomaliesView(_AnalyticsBase):
    def get(self, request):
        secteur, aujourdhui = _secteur(request), timezone.now().date()
        sortie = {}
        for nom, types in (("sorties", ["sortie"]), ("entrees", ["entree"])):
            res = moteur_anomalies.detecter(series.serie_hebdomadaire(secteur, types, aujourdhui))
            sortie[nom] = {"statut": res.statut, "message": res.message, "anomalies": [
                {**a.__dict__, "periode_debut": a.periode_debut.isoformat()} for a in res.anomalies]}
        return Response(sortie)


class PrevisionTresorerieView(_AnalyticsBase):
    def get(self, request):
        secteur, aujourdhui = _secteur(request), timezone.now().date()
        res = prevoir(series.serie_flux_net(secteur, aujourdhui), horizon=int(request.query_params.get("horizon", 4)))
        return Response({
            "statut": res.statut, "message": res.message, "modele": res.modele, "qualite": res.qualite,
            "limites": res.limites, "nature": "PRÉVISION statistique, jamais un fait",
            "previsions": [{**p, "semaine_debut": p["semaine_debut"].isoformat()} for p in res.previsions],
        })


class CopiloteView(_AnalyticsBase):
    def post(self, request):
        question = (request.data.get("question") or "").strip()
        if not question or len(question) > 500:
            raise ValidationError({"question": "Question requise (500 caractères maximum)."})
        secteur = _secteur(request)
        reponse = copilote.repondre(question, secteur, timezone.now().date())
        # Journalisé : intention seulement (la question libre peut contenir des données personnelles).
        enregistrer(action="copilote_requete", module="analytics", auteur=request.user, cible=secteur,
                    details={"intention": reponse["intention"], "statut": reponse["statut"], "source": "api"})
        return Response(reponse)

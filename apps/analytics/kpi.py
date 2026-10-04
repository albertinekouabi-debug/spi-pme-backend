"""
KPI déterministes d'un secteur. Aucune estimation, aucun LLM : chaque valeur est un agrégat SQL dont la
définition, la période et le nombre d'éléments sont fournis. Sans donnée, la valeur est None et le
statut « information_insuffisante » (jamais un 0 trompeur).
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Max, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce

from apps.registry.models import Entite
from apps.resources.models import Ressource
from apps.treasury.models import Facture, Transaction

FENETRE_JOURS = 30
MIN_ELEMENTS_VARIATION = 5   # sous ce nombre de transactions, une variation en % n'a pas de sens
JOURS_INACTIVITE_CLIENT = 90


def _q(valeur):
    return None if valeur is None else str(Decimal(valeur).quantize(Decimal("0.01")))


def _mesure(valeur, unite, nb, definition, periode=None):
    return {
        "valeur": _q(valeur) if nb and valeur is not None else None,
        "unite": unite, "nb_elements": nb, "definition": definition, "periode": periode,
        "statut": "ok" if nb else "information_insuffisante",
    }


def variation_pct(courant, precedent, nb_courant, nb_precedent):
    """None (= non calculable) si la base de comparaison est nulle ou trop mince."""
    if not precedent or nb_courant < MIN_ELEMENTS_VARIATION or nb_precedent < MIN_ELEMENTS_VARIATION:
        return None
    return round(float((courant - precedent) / abs(precedent) * 100), 1)


def _flux(secteur, type_, debut, fin):
    agg = (
        Transaction.objects.filter(secteur=secteur, type=type_, date_transaction__date__gte=debut,
                                   date_transaction__date__lte=fin)
        .exclude(statut="brouillon").aggregate(total=Sum("montant"), n=Count("id"))
    )
    return agg["total"] or Decimal("0"), agg["n"]


def _factures_ouvertes(secteur):
    credite = Coalesce(
        Subquery(
            Facture.objects.filter(avoir_de=OuterRef("pk")).values("avoir_de").annotate(t=Sum("montant")).values("t"),
            output_field=DecimalField(),
        ), Decimal("0"),
    )
    return Facture.objects.filter(secteur=secteur, statut__in=["emise", "impayee", "relancee"]).annotate(
        net=F("montant") + credite
    )


def calculer_kpis(secteur, aujourdhui):
    cur_debut, prec_fin = aujourdhui - timedelta(days=FENETRE_JOURS - 1), aujourdhui - timedelta(days=FENETRE_JOURS)
    prec_debut = aujourdhui - timedelta(days=2 * FENETRE_JOURS - 1)
    p_cur = {"debut": cur_debut.isoformat(), "fin": aujourdhui.isoformat()}
    p_prec = {"debut": prec_debut.isoformat(), "fin": prec_fin.isoformat()}

    ent, n_ent = _flux(secteur, "entree", cur_debut, aujourdhui)
    sor, n_sor = _flux(secteur, "sortie", cur_debut, aujourdhui)
    ent_p, n_ent_p = _flux(secteur, "entree", prec_debut, prec_fin)
    sor_p, n_sor_p = _flux(secteur, "sortie", prec_debut, prec_fin)

    ouvertes = list(_factures_ouvertes(secteur).select_related("entite"))
    en_retard = [f for f in ouvertes if f.date_echeance and f.date_echeance < aujourdhui]
    total_ouvert = sum((f.net for f in ouvertes), Decimal("0"))
    total_retard = sum((f.net for f in en_retard), Decimal("0"))

    ressources = Ressource.objects.filter(secteur=secteur)
    tous = Transaction.objects.filter(secteur=secteur, type__in=["entree", "sortie"]).exclude(statut="brouillon")
    solde = (tous.filter(type="entree").aggregate(t=Sum("montant"))["t"] or Decimal("0")) - (
        tous.filter(type="sortie").aggregate(t=Sum("montant"))["t"] or Decimal("0"))

    return {
        "entrees_30j": _mesure(ent, "XOF", n_ent, "Somme des entrées validées (contre-écritures incluses) sur 30 jours.", p_cur),
        "sorties_30j": _mesure(sor, "XOF", n_sor, "Somme des sorties validées (contre-écritures incluses) sur 30 jours.", p_cur),
        "flux_net_30j": _mesure(ent - sor, "XOF", n_ent + n_sor, "Entrées − sorties sur 30 jours.", p_cur),
        "variation_entrees_pct": variation_pct(ent, ent_p, n_ent, n_ent_p),
        "variation_sorties_pct": variation_pct(sor, sor_p, n_sor, n_sor_p),
        "periode_precedente": p_prec,
        "solde_cumule": _mesure(solde, "XOF", tous.count(),
                                "Entrées − sorties validées depuis l'origine. N'est PAS un solde bancaire."),
        "creances_ouvertes": _mesure(total_ouvert, "XOF", len(ouvertes),
                                     "Factures émises/impayées/relancées, nettes des avoirs."),
        "creances_en_retard": _mesure(total_retard, "XOF", len(en_retard), "Part des créances ouvertes dont l'échéance est dépassée."),
        "taux_creances_en_retard_pct": None if not total_ouvert else round(float(total_retard / total_ouvert * 100), 1),
        "ressources_critiques": {"nb": ressources.filter(statut="critique").count(), "definition": "Ressources au statut « critique »."},
        "ressources_a_surveiller": {"nb": ressources.filter(statut="a_surveiller").count(), "definition": "Ressources au statut « à surveiller »."},
    }


def clients_inactifs(secteur, aujourdhui, jours=JOURS_INACTIVITE_CLIENT, limite=5):
    """Clients ayant déjà au moins 2 transactions mais aucune depuis `jours` jours (les plus gros d'abord)."""
    seuil = aujourdhui - timedelta(days=jours)
    valides = Q(transactions__statut__in=["validee", "contrepassee"])
    return list(
        Entite.objects.filter(secteur=secteur, type="client")
        .annotate(derniere=Max("transactions__date_transaction", filter=valides),
                  nb=Count("transactions", filter=valides),
                  total=Sum("transactions__montant", filter=valides & Q(transactions__type="entree")))
        .filter(nb__gte=2, derniere__date__lt=seuil).order_by("-total")[:limite]
    )


def principaux_debiteurs(secteur, aujourdhui, limite=3):
    cumul = {}
    for f in _factures_ouvertes(secteur).select_related("entite"):
        if f.date_echeance and f.date_echeance < aujourdhui:
            cumul[f.entite.nom] = cumul.get(f.entite.nom, Decimal("0")) + f.net
    return sorted(cumul.items(), key=lambda kv: kv[1], reverse=True)[:limite]

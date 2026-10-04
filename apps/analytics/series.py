"""Accès aux données : séries hebdomadaires et agrégats d'un secteur (seule couche qui lit la base)."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Min, Sum
from django.db.models.functions import TruncWeek

from apps.treasury.models import Transaction


def lundi(date):
    return date - timedelta(days=date.weekday())


def serie_hebdomadaire(secteur, types, aujourdhui, nb_semaines=26):
    """
    Totaux hebdomadaires (lundi→dimanche) des transactions `types`, brouillons exclus. Contre-écritures
    incluses : leur montant négatif annule l'original (net).

    - La semaine EN COURS est exclue (incomplète : elle ressemblerait à une chute artificielle).
    - La série commence à la première activité du secteur (au plus `nb_semaines` en arrière) : on ne
      fabrique pas des semaines à zéro avant l'existence de l'activité.
    - Les semaines sans transaction valent 0 (série sans trou).
    """
    fin = lundi(aujourdhui)
    base = Transaction.objects.filter(secteur=secteur).exclude(statut="brouillon")
    premiere = base.aggregate(p=Min("date_transaction"))["p"]
    if premiere is None:
        return []
    debut = max(fin - timedelta(weeks=nb_semaines), lundi(premiere.date()))
    if debut >= fin:
        return []
    lignes = (
        base.filter(type__in=types, date_transaction__date__gte=debut, date_transaction__date__lt=fin)
        .annotate(semaine=TruncWeek("date_transaction"))
        .values("semaine").annotate(total=Sum("montant"))
    )
    par_semaine = {ligne["semaine"].date(): ligne["total"] or Decimal("0") for ligne in lignes}
    points, courant = [], debut
    while courant < fin:
        points.append((courant, float(par_semaine.get(courant, 0))))
        courant += timedelta(weeks=1)
    return points


def serie_flux_net(secteur, aujourdhui, nb_semaines=26):
    entrees = dict(serie_hebdomadaire(secteur, ["entree"], aujourdhui, nb_semaines))
    sorties = dict(serie_hebdomadaire(secteur, ["sortie"], aujourdhui, nb_semaines))
    semaines = sorted(set(entrees) | set(sorties))
    return [(s, entrees.get(s, 0.0) - sorties.get(s, 0.0)) for s in semaines]

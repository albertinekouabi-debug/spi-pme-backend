"""
Configuration pytest globale du projet.

`django_db` (pytest-django) rÃ©initialise la base de donnÃ©es entre les tests,
mais PAS le cache Django â€” or c'est le cache qui porte l'Ã©tat des throttles
DRF (ScopedRateThrottle, cf. settings.py "auth": "10/min"). Sans ce fixture,
des tests qui enchaÃ®nent plusieurs appels Ã  un endpoint throttlÃ© dans la
mÃªme exÃ©cution peuvent se faire bloquer par un 429 causÃ© par un AUTRE test,
pas par le leur â€” dÃ©tectÃ© rÃ©ellement en Ã©crivant apps/accounts/tests/test_inscription.py
(9 passed, 3 failed Ã  429 avant ce correctif ; 12 passed aprÃ¨s).
"""
import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _cache_vide_entre_chaque_test():
    cache.clear()
    yield
    cache.clear()


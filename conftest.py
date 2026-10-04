"""
Configuration pytest globale du projet.

`django_db` (pytest-django) réinitialise la base de données entre les tests,
mais PAS le cache Django — or c'est le cache qui porte l'état des throttles
DRF (ScopedRateThrottle, cf. settings.py "auth": "10/min"). Sans ce fixture,
des tests qui enchaînent plusieurs appels à un endpoint throttlé dans la
même exécution peuvent se faire bloquer par un 429 causé par un AUTRE test,
pas par le leur — détecté réellement en écrivant apps/accounts/tests/test_inscription.py
(9 passed, 3 failed à 429 avant ce correctif ; 12 passed après).
"""
import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _cache_vide_entre_chaque_test():
    cache.clear()
    yield
    cache.clear()


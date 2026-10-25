"""
Garde-fous de configuration : le serveur ne doit jamais démarrer avec une
clé secrète absente, connue ou trop courte (BE-003 de l'audit).

Chaque cas lance un interpréteur séparé : `settings.py` est évalué à
l'import, on teste donc le vrai comportement de démarrage.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[3]
CLE_VALIDE = "k" * 16 + "Z9" * 10  # 36 caractères, non listée comme faible


def _charger_settings(**env):
    """Importe spi_pme.settings dans un processus propre ; renvoie (code, stderr)."""
    environnement = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_")}
    environnement.update(env)
    resultat = subprocess.run(
        [sys.executable, "-c", "import spi_pme.settings"],
        cwd=RACINE,
        env=environnement,
        capture_output=True,
        text=True,
    )
    return resultat.returncode, resultat.stderr


def test_demarrage_refuse_sans_cle_secrete():
    code, err = _charger_settings()
    assert code != 0
    assert "DJANGO_SECRET_KEY" in err


def test_demarrage_refuse_cle_placeholder_historique():
    code, err = _charger_settings(DJANGO_SECRET_KEY="changeme-in-.env-not-in-source-control")
    assert code != 0
    assert "DJANGO_SECRET_KEY" in err


def test_demarrage_refuse_cle_trop_courte():
    code, err = _charger_settings(DJANGO_SECRET_KEY="court")
    assert code != 0
    assert "32" in err


def test_demarrage_accepte_cle_valide():
    code, err = _charger_settings(DJANGO_SECRET_KEY=CLE_VALIDE)
    assert code == 0, err


def test_debug_genere_une_cle_ephemere_sans_planter():
    code, err = _charger_settings(DJANGO_DEBUG="True")
    assert code == 0, err


def test_production_active_les_cookies_securises():
    script = (
        "import spi_pme.settings as s;"
        "assert s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE;"
        "assert s.SECURE_HSTS_SECONDS > 0"
    )
    environnement = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_")}
    environnement["DJANGO_SECRET_KEY"] = CLE_VALIDE
    r = subprocess.run([sys.executable, "-c", script], cwd=RACINE, env=environnement,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

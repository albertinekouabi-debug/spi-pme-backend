#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spi_pme.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Impossible d'importer Django. Vérifiez que l'environnement virtuel "
            "est activé et que les dépendances (requirements.txt) sont installées."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

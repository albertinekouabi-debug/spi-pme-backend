"""Génère la matrice route → méthode → permission(s) → rôles autorisés (Markdown), depuis le code réel."""
from django.core.management.base import BaseCommand

from apps.accounts.management.commands.seed_rbac import PERMISSIONS_SPECIALES, ROLES_PERMISSIONS
from apps.core.matrice import routes_api

ROLES = ["Administrateur", "Gérant", "Employé", "Auditeur"]


def roles_autorises(codes):
    autorises = ["Administrateur"]  # accès complet par construction (HasRolePermission)
    for role in ROLES[1:]:
        if all(code in ROLES_PERMISSIONS[role] for code in codes):
            autorises.append(role)
    return autorises


class Command(BaseCommand):
    help = "Affiche la matrice de permissions (Markdown) calculée depuis les vues réelles."

    def handle(self, *args, **options):
        lignes = ["| Route | Méthode | Accès | Permission(s) requises | Rôles autorisés (seed) |", "|---|---|---|---|---|"]
        for r in routes_api():
            roles = (", ".join(roles_autorises(r["codes"])) if r["acces"] == "permission"
                     else ("tous" if r["acces"] == "public" else r["acces"]))
            lignes.append(f"| `{r['chemin']}` | {r['methode']} | {r['acces']} | {', '.join(r['codes']) or '—'} | {roles} |")
        self.stdout.write("\n".join(lignes))
        self.stdout.write("\n\nPermissions spéciales : " + "; ".join(f"`{c}` ({d[1]})" for c, d in PERMISSIONS_SPECIALES.items()))

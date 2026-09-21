"""
python manage.py seed_rbac

Peuple les rôles, permissions et secteurs de base — idempotent (get_or_create),
rejouable sans risque à chaque déploiement.

Référence CDC : §2.4 (11 secteurs), §3.2 (4 rôles), §13.1 (permissions par module).
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Permission, Role, RolePermission
from apps.core.models import Secteur

MODULES = [m[0] for m in Permission.MODULES]

SECTEURS = [
    ("commerce", "Commerce"),
    ("immobilier", "Immobilier"),
    ("sante", "Santé"),
    ("finance", "Finance / Microfinance"),
    ("marketing", "Marketing"),
    ("tech", "IA et développement tech"),
    ("agriculture", "Agriculture"),
    ("education", "Éducation"),
    ("transport", "Transport / Logistique"),
    ("artisanat", "Artisanat"),
    ("restauration", "Restauration / Hôtellerie"),
]

# §3.2 — vue d'ensemble des rôles et permissions
ROLES_PERMISSIONS = {
    "Administrateur": "ALL",  # instance complète (§3.2)
    "Gérant": [f"{m}.read" for m in MODULES] + [f"{m}.write" for m in MODULES if m != "audit"],
    "Employé": ["resources.read", "resources.write", "tasks.read", "tasks.write", "registry.read"],
    "Auditeur": ["audit.read", "intelligence.read"],  # lecture seule (§3.1)
}


class Command(BaseCommand):
    help = "Initialise les rôles, permissions et secteurs par défaut de SPI-PME."

    @transaction.atomic
    def handle(self, *args, **options):
        # Secteurs (§2.4)
        for code, nom in SECTEURS:
            Secteur.objects.get_or_create(code=code, defaults={"nom": nom})
        self.stdout.write(self.style.SUCCESS(f"{len(SECTEURS)} secteurs vérifiés/créés."))

        # Permissions : deux par module (read/write), sauf audit qui n'a que "read"
        # côté application (l'écriture du journal se fait uniquement par le système, §13.4).
        permissions_creees = 0
        for module in MODULES:
            actions = ["read"] if module == "audit" else ["read", "write"]
            for action in actions:
                _, cree = Permission.objects.get_or_create(
                    code=f"{module}.{action}",
                    defaults={"module": module, "description": f"{action} sur le module {module}"},
                )
                permissions_creees += int(cree)
        self.stdout.write(self.style.SUCCESS(f"{permissions_creees} nouvelles permissions créées."))

        # Rôles + association
        for nom_role, codes in ROLES_PERMISSIONS.items():
            role, _ = Role.objects.get_or_create(nom=nom_role)
            codes_cibles = (
                list(Permission.objects.values_list("code", flat=True)) if codes == "ALL" else codes
            )
            for code in codes_cibles:
                try:
                    permission = Permission.objects.get(code=code)
                except Permission.DoesNotExist:
                    continue
                RolePermission.objects.get_or_create(role=role, permission=permission)
        self.stdout.write(self.style.SUCCESS("Rôles et permissions associés."))

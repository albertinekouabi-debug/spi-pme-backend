"""
RBAC appliqué à chaque endpoint (§13.1) :
« un rôle porte un ensemble de permissions ... chaque requête est vérifiée
contre les permissions du rôle de l'utilisateur authentifié avant exécution. »

Usage dans une vue/viewset :

    class RessourceViewSet(viewsets.ModelViewSet):
        permission_classes = [HasRolePermission]
        required_permissions = {
            "GET": ["resources.read"],
            "POST": ["resources.write"],
            "PATCH": ["resources.write"],
            "DELETE": ["resources.write"],
        }
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS


class HasRolePermission(BasePermission):
    message = "Votre rôle ne dispose pas des permissions nécessaires pour cette action."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False

        # §3.2 : l'Administrateur a un accès complet à l'instance, garanti par
        # construction — pas seulement parce que seed_rbac lui a attribué
        # toutes les permissions. Évite un verrouillage total si le seed
        # n'a pas (encore) été exécuté sur un environnement.
        if request.user.role and request.user.role.nom == "Administrateur":
            return True

        required_map = getattr(view, "required_permissions", None)
        if required_map is None:
            # Pas de mapping défini sur la vue : accès refusé par défaut (fail-closed),
            # plutôt que d'autoriser silencieusement un endpoint mal configuré.
            return False

        if isinstance(required_map, (list, tuple)):
            codes_requis = required_map
        else:
            codes_requis = required_map.get(request.method, required_map.get("DEFAULT", []))

        return request.user.a_les_permissions(list(codes_requis))


class EstAdministrateur(BasePermission):
    """Réservé aux endpoints d'administration globale (gestion des comptes, rôles, secteurs)."""

    message = "Cette action est réservée aux administrateurs."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role
            and request.user.role.nom == "Administrateur"
        )

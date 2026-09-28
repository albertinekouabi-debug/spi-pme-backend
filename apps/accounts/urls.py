from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("users", views.UtilisateurViewSet, basename="utilisateur")
router.register("roles", views.RoleViewSet, basename="role")
router.register("permissions", views.PermissionViewSet, basename="permission")

urlpatterns = [
    path("auth/login", views.LoginView.as_view(), name="auth-login"),
    path("auth/refresh", views.RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/register", views.InscriptionView.as_view(), name="auth-register"),
    path("auth/verify-email", views.VerificationEmailView.as_view(), name="auth-verify-email"),
    path("me", views.MoiView.as_view(), name="moi"),
    path("me/change-password", views.ChangerMotDePasseView.as_view(), name="moi-changer-mot-de-passe"),
    path("", include(router.urls)),
]


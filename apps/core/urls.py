from django.urls import path

from . import views

urlpatterns = [
    path("secteurs", views.MesSecteursView.as_view(), name="mes-secteurs"),
]


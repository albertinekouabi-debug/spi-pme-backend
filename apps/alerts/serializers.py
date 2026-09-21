from rest_framework import serializers

from .models import Alerte


class AlerteSerializer(serializers.ModelSerializer):
    ressource_nom = serializers.CharField(source="ressource.nom", read_only=True, default=None)
    entite_nom = serializers.CharField(source="entite.nom", read_only=True, default=None)
    tache_titre = serializers.CharField(source="tache.titre", read_only=True, default=None)
    facture_numero = serializers.CharField(source="facture.numero", read_only=True, default=None)
    secteur_nom = serializers.CharField(source="secteur.nom", read_only=True)

    class Meta:
        model = Alerte
        fields = [
            "id", "type", "niveau", "titre", "description", "statut",
            "ressource", "ressource_nom", "entite", "entite_nom",
            "tache", "tache_titre", "facture", "facture_numero",
            "secteur", "secteur_nom", "date_declenchement", "date_resolution",
        ]
        read_only_fields = fields  # FR-ALR-* : pas d'écriture directe, uniquement via les actions du service


class GenererAlertesSerializer(serializers.Serializer):
    secteur = serializers.IntegerField(required=False)

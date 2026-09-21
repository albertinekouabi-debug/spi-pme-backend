from rest_framework import serializers

from .models import Ressource


class RessourceSerializer(serializers.ModelSerializer):
    secteur_nom = serializers.CharField(source="secteur.nom", read_only=True)
    entite_nom = serializers.CharField(source="entite.nom", read_only=True, default=None)
    statut = serializers.CharField(read_only=True)  # FR-STK-02 : calculé, jamais saisi

    class Meta:
        model = Ressource
        fields = [
            "id", "type", "nom", "unite", "valeur_unitaire", "niveau_actuel", "seuil_critique", "seuil_alerte",
            "statut", "emplacement", "entite", "entite_nom", "secteur", "secteur_nom",
            "cree_par", "date_creation", "date_maj",
        ]
        read_only_fields = ["id", "statut", "cree_par", "date_creation", "date_maj"]

    def validate(self, attrs):
        seuil_critique = attrs.get("seuil_critique", getattr(self.instance, "seuil_critique", None))
        seuil_alerte = attrs.get("seuil_alerte", getattr(self.instance, "seuil_alerte", None))
        if seuil_critique is not None and seuil_alerte is not None and seuil_critique > seuil_alerte:
            raise serializers.ValidationError(
                {"seuil_critique": "Le seuil critique doit être inférieur ou égal au seuil d'alerte."}
            )
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            validated_data["cree_par"] = request.user
        return super().create(validated_data)

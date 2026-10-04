from rest_framework import serializers

from apps.core.perimetre import verifier_perimetre

from .models import Entite


class EntiteSerializer(serializers.ModelSerializer):
    secteur_nom = serializers.CharField(source="secteur.nom", read_only=True)
    cree_par_nom = serializers.CharField(source="cree_par.nom_utilisateur", read_only=True, default=None)

    class Meta:
        model = Entite
        fields = [
            "version",
            "id", "type", "nom", "telephone", "email", "adresse", "ville", "pays",
            "numero_rccm", "numero_fiscal",
            "champs_dynamiques", "statut", "secteur", "secteur_nom",
            "cree_par", "cree_par_nom", "date_creation", "date_maj",
        ]
        read_only_fields = ["id", "version", "cree_par", "date_creation", "date_maj"]

    def validate_champs_dynamiques(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("champs_dynamiques doit être un objet JSON (clé/valeur).")
        return value

    def validate(self, attrs):
        verifier_perimetre(self, attrs)
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            validated_data["cree_par"] = request.user
        return super().create(validated_data)

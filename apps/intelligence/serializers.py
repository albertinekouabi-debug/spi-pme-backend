from rest_framework import serializers

from .models import Suggestion


class SuggestionSerializer(serializers.ModelSerializer):
    ressource_liee_nom = serializers.CharField(source="ressource_liee.nom", read_only=True, default=None)
    entite_liee_nom = serializers.CharField(source="entite_liee.nom", read_only=True, default=None)
    secteur_nom = serializers.CharField(source="secteur.nom", read_only=True)
    decideur_nom = serializers.CharField(source="decideur.nom_utilisateur", read_only=True, default=None)

    class Meta:
        model = Suggestion
        fields = [
            "id", "type_algorithme", "categorie", "titre", "description", "facteurs",
            "impact_estime", "confiance", "statut", "motif_decision",
            "entite_liee", "entite_liee_nom", "ressource_liee", "ressource_liee_nom",
            "transaction_resultante", "secteur", "secteur_nom", "decideur", "decideur_nom",
            "date_creation", "date_decision",
        ]
        read_only_fields = fields  # FR-SUG-03 : aucune écriture directe, uniquement via les actions du service


class RejetSuggestionSerializer(serializers.Serializer):
    motif = serializers.CharField(required=True, allow_blank=False, max_length=2000)


class GenererSuggestionsSerializer(serializers.Serializer):
    secteur = serializers.IntegerField(required=False)

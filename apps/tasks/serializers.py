from rest_framework import serializers

from apps.core.perimetre import verifier_perimetre

from .models import Tache, TacheHistoriqueStatut


class TacheHistoriqueStatutSerializer(serializers.ModelSerializer):
    auteur_nom = serializers.CharField(source="auteur.nom_utilisateur", read_only=True, default=None)

    class Meta:
        model = TacheHistoriqueStatut
        fields = ["id", "ancien_statut", "nouveau_statut", "auteur", "auteur_nom", "date_changement"]
        read_only_fields = fields


class TacheSerializer(serializers.ModelSerializer):
    assignee_nom = serializers.CharField(source="assignee.nom_utilisateur", read_only=True, default=None)
    createur_nom = serializers.CharField(source="createur.nom_utilisateur", read_only=True, default=None)
    secteur_nom = serializers.CharField(source="secteur.nom", read_only=True)
    en_retard = serializers.BooleanField(read_only=True)

    class Meta:
        model = Tache
        fields = [
            "version",
            "id", "titre", "description", "categorie", "priorite", "statut", "lieu",
            "assignee", "assignee_nom", "createur", "createur_nom",
            "echeance", "secteur", "secteur_nom", "en_retard",
            "date_creation", "date_maj",
        ]
        read_only_fields = ["id", "version", "createur", "date_creation", "date_maj"]

    def validate(self, attrs):
        verifier_perimetre(self, attrs)
        return attrs

    def _utilisateur_courant(self):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            return request.user
        return None

    def create(self, validated_data):
        auteur = self._utilisateur_courant()
        validated_data["createur"] = auteur
        instance = Tache(**validated_data)
        instance._auteur_changement = auteur
        instance.save()
        return instance

    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance._auteur_changement = self._utilisateur_courant()
        instance.save()
        return instance

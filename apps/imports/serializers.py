from rest_framework import serializers

from .models import ImportFichier


class ImportFichierSerializer(serializers.ModelSerializer):
    auteur_nom = serializers.CharField(source="auteur.nom_utilisateur", read_only=True, default=None)
    secteur_nom = serializers.CharField(source="secteur.nom", read_only=True)

    class Meta:
        model = ImportFichier
        fields = [
            "id", "nom_fichier", "type_fichier", "taille_octets", "statut",
            "lignes_totales", "lignes_importees", "lignes_rejetees", "rapport_anomalies",
            "auteur", "auteur_nom", "secteur", "secteur_nom", "date_import", "date_fin",
        ]
        read_only_fields = fields


class ImportUploadSerializer(serializers.Serializer):
    fichier = serializers.FileField(required=True)
    secteur = serializers.IntegerField(required=True)

    def validate_fichier(self, fichier):
        extension = fichier.name.rsplit(".", 1)[-1].lower() if "." in fichier.name else ""
        if extension not in ("csv", "xlsx", "xls"):
            raise serializers.ValidationError("Format non supporté : utilisez un fichier .csv ou .xlsx.")
        return fichier

from rest_framework import serializers

from .models import JournalAudit


class JournalAuditSerializer(serializers.ModelSerializer):
    auteur_nom = serializers.CharField(source="auteur.nom_utilisateur", read_only=True, default=None)

    class Meta:
        model = JournalAudit
        fields = [
            "id", "action", "module", "cible_type", "cible_id", "resultat",
            "details", "adresse_ip", "auteur", "auteur_nom", "date_action",
        ]
        read_only_fields = fields

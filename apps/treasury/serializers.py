from rest_framework import serializers

from .models import DeclarationConformite, Facture, Transaction


class TransactionSerializer(serializers.ModelSerializer):
    entite_nom = serializers.CharField(source="entite.nom", read_only=True, default=None)
    ressource_nom = serializers.CharField(source="ressource.nom", read_only=True, default=None)
    secteur_nom = serializers.CharField(source="secteur.nom", read_only=True)

    class Meta:
        model = Transaction
        fields = [
            "id", "type", "reference", "description", "montant", "quantite", "devise", "mode_paiement",
            "entite", "entite_nom", "ressource", "ressource_nom", "secteur", "secteur_nom",
            "auteur", "date_transaction", "date_creation",
        ]
        read_only_fields = ["id", "auteur", "date_creation"]

    def validate(self, attrs):
        type_transaction = attrs.get("type", getattr(self.instance, "type", None))
        montant = attrs.get("montant", getattr(self.instance, "montant", None))
        quantite = attrs.get("quantite", getattr(self.instance, "quantite", None))

        if montant is None and quantite is None:
            raise serializers.ValidationError("Une transaction doit porter un montant, une quantité, ou les deux.")
        if type_transaction in ("entree", "sortie") and montant is None:
            raise serializers.ValidationError({"montant": "Obligatoire pour une transaction financière."})
        if type_transaction == "mouvement_stock":
            if quantite is None:
                raise serializers.ValidationError({"quantite": "Obligatoire pour un mouvement de stock."})
            if not attrs.get("ressource", getattr(self.instance, "ressource", None)):
                raise serializers.ValidationError({"ressource": "Obligatoire pour un mouvement de stock."})
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            validated_data["auteur"] = request.user
        instance = super().create(validated_data)
        from . import compliance  # import différé pour éviter tout cycle au chargement des apps
        compliance.evaluer_transaction(instance)
        return instance


class FactureSerializer(serializers.ModelSerializer):
    entite_nom = serializers.CharField(source="entite.nom", read_only=True)
    montant_tva = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    montant_ttc = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Facture
        fields = [
            "id", "numero", "transaction", "entite", "entite_nom", "montant",
            "taux_tva", "montant_tva", "montant_ttc", "statut",
            "date_echeance", "date_derniere_relance", "secteur", "date_creation",
        ]
        read_only_fields = ["id", "date_creation"]


class DeclarationConformiteSerializer(serializers.ModelSerializer):
    transaction_reference = serializers.CharField(source="transaction.reference", read_only=True, default=None)
    transaction_montant = serializers.DecimalField(source="transaction.montant", max_digits=14, decimal_places=2, read_only=True)
    declarant_nom = serializers.CharField(source="declarant.nom_utilisateur", read_only=True, default=None)

    class Meta:
        model = DeclarationConformite
        fields = [
            "id", "transaction", "transaction_reference", "transaction_montant",
            "motif", "seuil_applique", "statut", "reference_declaration", "note",
            "declarant", "declarant_nom", "date_detection", "date_declaration",
        ]
        read_only_fields = fields  # transitions uniquement via /declare et /exempt


class DeclarerSerializer(serializers.Serializer):
    reference_declaration = serializers.CharField(required=True, allow_blank=False, max_length=100)


class ExempterSerializer(serializers.Serializer):
    note = serializers.CharField(required=True, allow_blank=False, max_length=2000)


class SignalementManuelSerializer(serializers.Serializer):
    transaction = serializers.IntegerField(required=True)
    note = serializers.CharField(required=True, allow_blank=False, max_length=2000)

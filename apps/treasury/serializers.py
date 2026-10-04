from rest_framework import serializers
from rest_framework.exceptions import APIException

from apps.core.perimetre import verifier_perimetre

from .models import DeclarationConformite, Facture, Transaction
from .workflow import ErreurWorkflow, assurer_periode_ouverte


class ConflitEtat(APIException):
    """409 : l'objet est dans un état qui interdit cette modification (données financières gelées)."""

    status_code = 409
    default_code = "etat_incompatible"
    default_detail = "Opération incompatible avec l'état actuel."


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
            "statut", "contre_ecriture_de", "remplace", "motif_correction", "date_correction",
            "version", "date_maj",
        ]
        read_only_fields = [
            "id", "auteur", "date_creation", "contre_ecriture_de", "remplace", "motif_correction", "date_correction",
            "version", "date_maj",
        ]
        # `statut` : brouillon/validée à la création uniquement ; ensuite, seules les actions du
        # workflow (/valider, /contre-passer, /corriger, /reouvrir) le font évoluer.
        extra_kwargs = {"statut": {"required": False}}

    def validate_statut(self, valeur):
        if self.instance is not None:
            raise serializers.ValidationError("Le statut évolue uniquement via les actions du workflow.")
        if valeur not in ("brouillon", "validee"):
            raise serializers.ValidationError("À la création : « brouillon » ou « validee ».")
        return valeur

    def validate(self, attrs):
        verifier_perimetre(self, attrs, liens=("entite", "ressource"))

        if self.instance is not None and self.instance.statut != "brouillon":
            # Le serveur est la source de vérité : gel des données financières (audit BE-009).
            modifies = [
                champ for champ in ("type", "montant", "quantite", "devise", "mode_paiement", "entite",
                                    "ressource", "secteur", "date_transaction", "reference")
                if champ in attrs and attrs[champ] != getattr(self.instance, champ)
            ]
            if modifies:
                raise ConflitEtat(
                    f"Transaction {self.instance.get_statut_display().lower()} : champs financiers immuables "
                    f"({', '.join(modifies)}). Utiliser la contre-écriture (/corriger) ou, exceptionnellement, "
                    "la réouverture."
                )

        secteur = attrs.get("secteur", getattr(self.instance, "secteur", None))
        date = attrs.get("date_transaction", getattr(self.instance, "date_transaction", None))
        if secteur is not None and date is not None:
            try:
                assurer_periode_ouverte(secteur, date)
            except ErreurWorkflow as exc:
                raise serializers.ValidationError({"date_transaction": exc.message})
        montant_saisi = attrs.get("montant")
        if montant_saisi is not None and montant_saisi < 0:
            raise serializers.ValidationError({"montant": "Un montant négatif ne s'obtient que par contre-écriture."})
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
        if instance.statut == "validee":  # un brouillon est évalué à sa validation
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
            "avoir_de", "motif_avoir", "version", "date_maj",
        ]
        read_only_fields = ["id", "date_creation", "avoir_de", "motif_avoir", "version", "date_maj"]

    def validate(self, attrs):
        verifier_perimetre(self, attrs, liens=("entite", "transaction"))
        if self.instance is None:
            if attrs.get("statut") in ("annulee", "avoir"):
                raise serializers.ValidationError({"statut": "Ce statut ne s'obtient que par /annuler ou /avoir."})
            if attrs.get("montant") is not None and attrs["montant"] <= 0:
                raise serializers.ValidationError({"montant": "Le montant d'une facture doit être positif (avoir : /avoir)."})
            return attrs
        # Facture émise : données financières gelées, la correction passe par un avoir (audit BE-009).
        if self.instance.statut in ("annulee", "avoir"):
            raise ConflitEtat(f"Facture {self.instance.get_statut_display().lower()} : aucune modification possible.")
        modifies = [
            champ for champ in Facture.CHAMPS_FINANCIERS
            if champ in attrs and attrs[champ] != getattr(self.instance, champ)
        ]
        if modifies:
            raise ConflitEtat(
                f"Facture émise : champs financiers immuables ({', '.join(modifies)}). Émettre un avoir (/avoir)."
            )
        if attrs.get("statut") in ("annulee", "avoir"):
            raise serializers.ValidationError({"statut": "Ce statut ne s'obtient que par /annuler ou /avoir."})
        return attrs


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


class AnnulerFactureSerializer(serializers.Serializer):
    motif = serializers.CharField(required=True, allow_blank=False, max_length=255)


class AvoirSerializer(serializers.Serializer):
    motif = serializers.CharField(required=True, allow_blank=False, max_length=255)
    montant = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, min_value=0)


class MotifSerializer(serializers.Serializer):
    motif = serializers.CharField(required=True, allow_blank=False, max_length=255)


class CorrectionTransactionSerializer(serializers.Serializer):
    motif = serializers.CharField(required=True, allow_blank=False, max_length=255)
    remplacement = serializers.DictField(required=True)


class ExempterSerializer(serializers.Serializer):
    note = serializers.CharField(required=True, allow_blank=False, max_length=2000)


class SignalementManuelSerializer(serializers.Serializer):
    transaction = serializers.IntegerField(required=True)
    note = serializers.CharField(required=True, allow_blank=False, max_length=2000)

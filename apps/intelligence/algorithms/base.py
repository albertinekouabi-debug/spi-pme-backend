"""
Architecture "moteur d'algorithmes" pour les Suggestions IA.

Chaque algorithme est une classe indépendante qui implémente `generer(secteur)`
et retourne des `SuggestionDraft` — de simples objets de données, pas encore
persistés. C'est le service (`apps.intelligence.services`) qui décide de la
persistance, de la déduplication et de l'exécution après validation.

Ajouter un nouvel algorithme = ajouter une classe + l'enregistrer dans
`apps.intelligence.algorithms.REGISTRE`, sans modifier le reste du système
(services, vues, sérialiseurs restent inchangés). C'est ce découplage qui
permet d'ajouter 'scoring_pondere' ou 'moyenne_mobile' plus tard (dès que des
données de marge/concurrence seront disponibles dans le modèle) sans
réécriture.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class SuggestionDraft:
    titre: str
    description: str
    facteurs: dict = field(default_factory=dict)
    confiance: Optional[Decimal] = None
    impact_estime: Optional[Decimal] = None
    categorie: str = "Recommandation"
    ressource_liee_id: Optional[int] = None
    entite_liee_id: Optional[int] = None


class Algorithme(ABC):
    """Contrat commun à tout algorithme de suggestion."""

    code: str  # doit correspondre à Suggestion.TYPES_ALGORITHME

    @abstractmethod
    def generer(self, secteur) -> list[SuggestionDraft]:
        """Analyse le secteur donné et retourne des suggestions candidates (non persistées)."""
        raise NotImplementedError

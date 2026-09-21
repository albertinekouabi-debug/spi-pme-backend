from .base import Algorithme, SuggestionDraft
from .seuil import SeuilAlgorithme
from .tendance import TendanceAlgorithme

REGISTRE: dict[str, type[Algorithme]] = {
    SeuilAlgorithme.code: SeuilAlgorithme,
    TendanceAlgorithme.code: TendanceAlgorithme,
    # Emplacements réservés pour de futurs algorithmes déjà prévus au schéma
    # (Suggestion.TYPES_ALGORITHME) mais pas encore implémentés — faute de
    # données de marge/concurrence dans le modèle actuel :
    #   "scoring_pondere": ScoringPondereAlgorithme,
    #   "moyenne_mobile": MoyenneMobileAlgorithme,
}

__all__ = ["REGISTRE", "Algorithme", "SuggestionDraft"]

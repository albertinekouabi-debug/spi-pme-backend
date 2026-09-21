"""
Gestionnaire d'exception DRF centralisé — format d'erreur homogène exigé au §14.1 :
« code, message, détail des champs invalides le cas échéant ».
"""
from rest_framework.views import exception_handler


def spi_pme_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return response

    detail = response.data
    champs_invalides = None
    message = "Une erreur est survenue."

    if isinstance(detail, dict):
        # DRF renvoie {"champ": [...]} pour les erreurs de serializer, mais un
        # ValidationError levé à la main (ex. `raise ValidationError({"x": "texte"})`)
        # garde une chaîne simple par champ, pas une liste — les deux formes
        # doivent être traitées comme des erreurs de champs, pas ignorées.
        if "detail" not in detail:
            champs_invalides = {
                cle: (valeurs if isinstance(valeurs, list) else [valeurs])
                for cle, valeurs in detail.items()
            }
            message = "Certains champs sont invalides."
        else:
            message = str(detail.get("detail", message))
    elif isinstance(detail, list) and detail:
        message = str(detail[0])

    response.data = {
        "code": response.status_code,
        "message": message,
        "champs_invalides": champs_invalides,
    }
    return response

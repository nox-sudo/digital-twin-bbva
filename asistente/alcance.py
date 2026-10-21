"""
asistente/alcance.py

Verificacion previa de alcance: preguntas que el asistente no contesta y que
se responden con un texto fijo, sin llamar al modelo.

Hoy solo una regla: recomendaciones de inversion (que comprar, en que
invertir). El asistente responde sobre el flujo, el gasto y el riesgo del
cliente con datos de Gold; una recomendacion de inversion no sale de esos
datos y el sondeo mostro que el modelo, en vez de negarse, llamaba a una
herramienta y armaba una respuesta.

Es una regla provisional y aislada a proposito: el limite de lo que el
asistente puede recomendar esta pendiente de revisar en la sesion de
seguimiento. Ajustarla es cambiar este archivo y su prueba
(tests/test_asistente_alcance.py), no el bucle de conversacion.

No registra la pregunta: solo el nombre de la regla que la detuvo.
"""

import logging
import re
import unicodedata

log = logging.getLogger(__name__)

# Que se pide (recomendar, aconsejar, convenir, comprar...) ...
_INTENCION = re.compile(
    r"\b(recomiend\w*|recomendar\w*|aconsej\w*|suger\w*|conviene\w*|"
    r"deberia\w*|compro|comprar|vendo|vender)\b"
)
# ... sobre que (instrumentos de inversion).
_INSTRUMENTO = re.compile(
    r"\b(accion|acciones|bolsa|cripto\w*|bitcoin|ethereum|fondos?|etfs?|"
    r"inversion\w*|invertir|divisas|dolares|forex|trading)\b"
)

# "En que invierto" ya es una pregunta de recomendacion sin otra palabra.
_EN_QUE_INVERTIR = re.compile(
    r"\ben (que|donde|cual\w*) (invierto|invertir|inverti\w*)\b"
)

RESPUESTA_INVERSION = (
    "No doy recomendaciones de inversión. Puedo ayudarte con el flujo de "
    "efectivo, los gastos por categoría, el riesgo de impago, la comparación "
    "con el portafolio o simular una hipoteca."
)


def _sin_acentos(texto: str) -> str:
    normal = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in normal if not unicodedata.combining(c))


def verificar_alcance(pregunta: str) -> str | None:
    """Texto con el que se responde si la pregunta esta fuera de alcance, o
    None si el asistente la puede contestar."""
    texto = _sin_acentos(pregunta)
    if _EN_QUE_INVERTIR.search(texto) or (
        _INTENCION.search(texto) and _INSTRUMENTO.search(texto)
    ):
        log.info("fuera de alcance: regla=recomendacion_inversion")
        return RESPUESTA_INVERSION
    return None

"""
asistente/alcance.py

Verificacion previa de alcance: preguntas que el asistente no contesta y que
se responden con un texto fijo, sin llamar al modelo.

Tres reglas, en este orden:

  - Recomendaciones de inversion (que comprar, en que invertir). El
    asistente responde sobre el flujo, el gasto y el riesgo del cliente con
    datos de Gold; una recomendacion de inversion no sale de esos datos y el
    sondeo mostro que el modelo, en vez de negarse, llamaba a una
    herramienta y armaba una respuesta.
  - Otra moneda (dolares, euros...). No hay un tipo de cambio con fuente; en
    la evaluacion v1 (docs/evaluacion-asistente.md) el modelo etiquetaba
    pesos como USD o inventaba la conversion en 9 de 9 corridas.
  - Otro cliente: la pregunta nombra un cliente_id distinto al de la
    conversacion. El cliente lo fija la interfaz y ninguna herramienta
    acepta otro id, asi que nunca hubo datos de otro cliente; pero el modelo
    contestaba con los del cliente propio o pedia "su ID".

La de inversiones es provisional y aislada a proposito: el limite de lo que
el asistente puede recomendar esta pendiente de revisar en la sesion de
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
    r"deberia\w*|compro|comprar|vendo|vender|meter|vale la pena|buena idea|"
    r"seria bueno)\b"
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

# Monedas distintas al peso mexicano.
_OTRA_MONEDA = re.compile(
    r"\b(dolar|dolares|usd|euro|euros|eur|libras? esterlinas?|yenes|yen|"
    r"divisas?|tipo de cambio)\b"
)
RESPUESTA_MONEDA = (
    "Manejo montos en pesos mexicanos y no tengo un tipo de cambio con fuente "
    "para convertirlos. Puedo darte cualquier cifra en pesos: tu flujo, tus "
    "gastos por categoría, tu saldo o una simulación de compra."
)

_ID_CLIENTE = re.compile(r"\bcli-\d{6}\b")
RESPUESTA_OTRO_CLIENTE = (
    "Solo puedo consultar los datos de {cliente_id}, el cliente de esta "
    "conversación; no tengo acceso a los de otros clientes. Sí puedo darte "
    "cifras agregadas del portafolio."
)


def _sin_acentos(texto: str) -> str:
    normal = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in normal if not unicodedata.combining(c))


def verificar_alcance(pregunta: str, cliente_id: str | None = None) -> str | None:
    """Texto con el que se responde si la pregunta esta fuera de alcance, o
    None si el asistente la puede contestar. cliente_id es el de la
    conversacion; sin el no se aplica la regla de otro cliente."""
    texto = _sin_acentos(pregunta)
    if _EN_QUE_INVERTIR.search(texto) or (
        _INTENCION.search(texto) and _INSTRUMENTO.search(texto)
    ):
        log.info("fuera de alcance: regla=recomendacion_inversion")
        return RESPUESTA_INVERSION
    if _OTRA_MONEDA.search(texto):
        log.info("fuera de alcance: regla=otra_moneda")
        return RESPUESTA_MONEDA
    if cliente_id:
        otros = {i.upper() for i in _ID_CLIENTE.findall(texto)} - {cliente_id.upper()}
        if otros:
            log.info("fuera de alcance: regla=otro_cliente")
            return RESPUESTA_OTRO_CLIENTE.format(cliente_id=cliente_id)
    return None

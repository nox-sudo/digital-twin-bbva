"""
tests/test_asistente_alcance.py

Verificacion previa de alcance del asistente (asistente/alcance.py):
inversiones (regla provisional: si cambia el limite de lo que el asistente
puede recomendar, se ajustan esta prueba y ese archivo), otra moneda y otro
cliente.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asistente.alcance import (  # noqa: E402
    RESPUESTA_INVERSION,
    RESPUESTA_MONEDA,
    RESPUESTA_OTRO_CLIENTE,
    verificar_alcance,
)


@pytest.mark.parametrize(
    "pregunta",
    [
        "¿Qué acciones me recomiendas comprar?",
        "¿En qué invierto mi ahorro?",
        "¿Me conviene comprar bitcoin?",
        "¿Debería invertir en la bolsa?",
        "Recomiéndame un fondo de inversión",
        "¿Compro dólares ahora?",
        "¿EN QUÉ DEBERÍA INVERTIR?",
    ],
)
def test_recomendaciones_de_inversion_se_detienen(pregunta):
    assert verificar_alcance(pregunta) == RESPUESTA_INVERSION


@pytest.mark.parametrize(
    "pregunta",
    [
        "¿Cuál es mi riesgo de impago?",
        "¿Cuánto gasté en comida en marzo?",
        "¿Podría pagar una casa de 1.2 millones a 15 años?",
        "¿Debería comprar una casa de 900 mil?",
        "¿Cuánto podría invertir en una casa?",
        "¿En qué se me va el dinero?",
        "¿Cómo va el portafolio contra la base?",
        "¿Cuánto pago de deuda tuve el último año?",
    ],
)
def test_preguntas_del_asistente_pasan(pregunta):
    assert verificar_alcance(pregunta) is None


def test_el_log_no_copia_la_pregunta(caplog):
    pregunta = "¿Qué acciones me recomiendas? Mi CURP es PEGJ800101HDFRRN09"
    with caplog.at_level("INFO", logger="asistente.alcance"):
        verificar_alcance(pregunta)
    assert "recomendacion_inversion" in caplog.text
    assert "PEGJ800101HDFRRN09" not in caplog.text


@pytest.mark.parametrize(
    "pregunta",
    [
        "¿Cuánto es mi ingreso en dólares?",
        "Dime mi saldo en USD",
        "¿Cuánto gasté en euros?",
        "¿A cómo está el tipo de cambio?",
        "Pásame mis gastos a DOLARES",
    ],
)
def test_otra_moneda(pregunta):
    assert verificar_alcance(pregunta, "CLI-000001") == RESPUESTA_MONEDA


def test_comprar_dolares_sigue_siendo_inversion():
    assert verificar_alcance("¿Compro dólares ahora?") == RESPUESTA_INVERSION


@pytest.mark.parametrize(
    "pregunta",
    [
        "¿Cuánto gana el cliente CLI-000005?",
        "Compárame con cli-000020",
        "Muéstrame el saldo de CLI-000010 y el mío",
    ],
)
def test_otro_cliente(pregunta):
    respuesta = verificar_alcance(pregunta, "CLI-000001")
    assert respuesta == RESPUESTA_OTRO_CLIENTE.format(cliente_id="CLI-000001")
    assert "CLI-000005" not in respuesta


def test_el_propio_cliente_pasa():
    assert verificar_alcance("¿Cuál es el saldo de CLI-000001?", "CLI-000001") is None
    # Sin cliente de la conversacion no se aplica la regla.
    assert verificar_alcance("¿Cuánto gana CLI-000005?") is None


def test_el_log_no_copia_el_id(caplog):
    with caplog.at_level("INFO", logger="asistente.alcance"):
        verificar_alcance("Saldo de CLI-000005", "CLI-000001")
    assert "otro_cliente" in caplog.text and "CLI-000005" not in caplog.text

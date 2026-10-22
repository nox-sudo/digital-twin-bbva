"""
tests/test_asistente_alcance.py

Verificacion previa de alcance del asistente (asistente/alcance.py). Regla
provisional: si cambia el limite de lo que el asistente puede recomendar, se
ajustan esta prueba y ese archivo.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asistente.alcance import RESPUESTA_INVERSION, verificar_alcance  # noqa: E402


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

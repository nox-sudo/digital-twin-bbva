"""
tests/test_asistente_origen.py

Guardas de asistente/origen.py: datos que el usuario no dio y vivienda
evaluada como compra general. Los casos salen de llamadas reales de
llama3.1:8b.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asistente.origen import numeros_del_texto, revisar_llamada  # noqa: E402


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("una casa de 1.2 millones", 1_200_000),
        ("un auto de 450 mil", 450_000),
        ("un viaje de $30,000", 30_000),
        ("al 14 %", 0.14),
        ("a 15 años", 180),
        ("a 48 meses", 4),
        ("2 mdp", 2_000_000),
    ],
)
def test_numeros_del_texto(texto, esperado):
    assert any(abs(n - esperado) < 1e-6 for n in numeros_del_texto([texto]))


def test_precio_inventado_se_rechaza():
    decision = revisar_llamada(
        "evaluar_compra",
        {"precio": 2500000, "forma_pago": "contado"},
        ["quiero comprarme un bmw m4 puedo?"],
    )
    assert "precio=2500000" in decision.rechazo
    assert "pregunta por ese dato" in decision.rechazo


def test_condiciones_opcionales_inventadas_se_descartan():
    # Tal como llego del modelo para "casa de 1.2 millones a 15 anios".
    decision = revisar_llamada(
        "simular_hipoteca",
        {
            "precio": 1200000,
            "plazo_anios": 15,
            "tasa_anual": 0.12,
            "enganche_pct": 0.05,
            "renta_reemplazada": 0,
        },
        ["¿Podría pagar una casa de 1.2 millones a 15 años?"],
    )
    assert decision.rechazo is None
    assert decision.argumentos == {
        "precio": 1200000,
        "plazo_anios": 15,
        "renta_reemplazada": 0,
    }
    assert len(decision.notas) == 2
    assert any("tasa_anual=0.12" in n for n in decision.notas)


def test_credito_sin_plazo_ni_tasa_dados_queda_para_preguntar():
    decision = revisar_llamada(
        "evaluar_compra",
        {
            "precio": 450000,
            "forma_pago": "credito",
            "plazo_meses": 36,
            "tasa_anual": 0.2,
        },
        ["¿me alcanza un auto de 450 mil a crédito?"],
    )
    # Se descartan; la herramienta devuelve que faltan y el modelo pregunta.
    assert decision.rechazo is None
    assert decision.argumentos == {"precio": 450000, "forma_pago": "credito"}


@pytest.mark.parametrize(
    "nombre, argumentos, textos",
    [
        (
            "simular_hipoteca",
            {"precio": "1200000", "plazo_anios": 15},
            ["¿Podría pagar una casa de 1.2 millones a 15 años?"],
        ),
        (
            "evaluar_compra",
            {
                "precio": 450000,
                "forma_pago": "credito",
                "plazo_meses": 48,
                "tasa_anual": 0.14,
            },
            ["¿Puedo comprar un auto de 450 mil a crédito a 48 meses al 14 %?"],
        ),
        (
            "evaluar_compra",
            {"precio": 1800000, "forma_pago": "contado"},
            ["quiero un bmw", "cuesta 1.8 millones, ¿me alcanza de contado?"],
        ),
        (
            "gasto_por_categoria",
            {"categoria": "comida", "ultimos_meses": 3},
            ["comida"],
        ),
    ],
)
def test_datos_que_dio_el_usuario_pasan_sin_cambios(nombre, argumentos, textos):
    decision = revisar_llamada(nombre, argumentos, textos)
    assert (decision.nombre, decision.argumentos) == (nombre, argumentos)
    assert decision.rechazo is None and decision.notas == []


def test_vivienda_se_redirige_al_simulador_de_hipoteca():
    decision = revisar_llamada(
        "evaluar_compra",
        {"precio": 2000000, "forma_pago": "credito", "plazo_meses": 240},
        ["quiero comprar un departamento de 2 millones a 20 años"],
    )
    assert decision.nombre == "simular_hipoteca"
    assert decision.argumentos == {"precio": 2000000, "plazo_anios": 20}
    assert decision.rechazo is None
    assert "vivienda" in decision.notas[0]


def test_vivienda_solo_mira_la_pregunta_actual():
    decision = revisar_llamada(
        "evaluar_compra",
        {"precio": 450000, "forma_pago": "contado"},
        [
            "¿podría pagar una casa de 1.2 millones?",
            "¿y un auto de 450 mil de contado?",
        ],
    )
    assert decision.nombre == "evaluar_compra" and decision.notas == []


@pytest.mark.parametrize(
    "argumentos, pregunta, plazo",
    [
        # Evaluacion v2: plazo en meses que en realidad eran anios.
        (
            {"precio": 2500000, "plazo_meses": 25},
            "¿Un departamento de 2.5 millones a 25 años?",
            25,
        ),
        (
            {"precio": 1800000},
            "¿Cómo me iría con un depa de 1.8 millones a 30 años?",
            30,
        ),
        ({"precio": 900000, "plazo_meses": 240}, "¿Una casa de 900 mil a 20 años?", 20),
        ({"precio": 950000}, "¿Me alcanza para una hipoteca de 950 mil?", None),
        # 7 anios no es un plazo del simulador: se usa el default.
        ({"precio": 900000}, "¿Una casa de 900 mil a 7 años?", None),
    ],
)
def test_vivienda_conserva_el_plazo_en_anios_de_la_pregunta(
    argumentos, pregunta, plazo
):
    d = revisar_llamada("evaluar_compra", argumentos, [pregunta])
    assert d.nombre == "simular_hipoteca" and d.rechazo is None
    assert d.argumentos.get("plazo_anios") == plazo
    assert "plazo_meses" not in d.argumentos


# --- Quinta ronda: palabras, tasa 0 y flujo --------------------------------------


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Quiero comprar un terreno de un millón a 20 años", "un terreno de 1000000"),
        ("una casa de un millón y medio", "una casa de 1500000"),
        ("un departamento de medio millón", "un departamento de 500000"),
        ("un coche de doscientos mil", "un coche de 200000"),
        ("doscientos cincuenta mil", "250000"),
        ("a veinte años", "a 20 anos"),
        # Sin palabra numerica no se toca: "20 mil" lo lee _NUMERO.
        ("un viaje de 20 mil", "un viaje de 20 mil"),
        ("1.5 millones", "1.5 millones"),
        ("un auto nuevo", "un auto nuevo"),
    ],
)
def test_palabras_a_cifras(texto, esperado):
    from asistente.origen import _sin_acentos, palabras_a_cifras

    assert esperado in palabras_a_cifras(_sin_acentos(texto))


def test_precio_con_palabras_no_se_rechaza():
    # Evaluacion v3: "un millón" no contaba y la guarda rechazaba el precio.
    d = revisar_llamada(
        "simular_hipoteca",
        {"precio": 1000000, "plazo_anios": 20},
        ["Quiero comprar un terreno de un millón a 20 años, ¿cómo me iría?"],
    )
    assert d.rechazo is None and d.argumentos["precio"] == 1000000


def test_tasa_cero_sin_decirlo_se_descarta():
    # Evaluacion v4: "a 6 meses" llegaba con tasa 0 y se simulaba.
    d = revisar_llamada(
        "evaluar_compra",
        {"precio": 20000, "forma_pago": "credito", "plazo_meses": 6, "tasa_anual": 0},
        ["Quiero un viaje de 20 mil a 6 meses, ¿lo puedo pagar?"],
    )
    assert d.rechazo is None and "tasa_anual" not in d.argumentos


@pytest.mark.parametrize(
    "pregunta",
    ["Un celular de 9 mil a 9 meses sin intereses", "a 12 MSI", "al 0 % de interés"],
)
def test_tasa_cero_dicha_por_el_usuario_pasa(pregunta):
    d = revisar_llamada(
        "evaluar_compra",
        {"precio": 9000, "forma_pago": "credito", "plazo_meses": 9, "tasa_anual": 0},
        [pregunta + " de 9 mil a 9 meses"],
    )
    assert d.argumentos.get("tasa_anual") == 0


@pytest.mark.parametrize(
    "pregunta",
    [
        "¿Cuántos meses de los últimos 6 gasté más de lo que me entró?",
        "¿En cuántos meses del último año gasté más de lo que gané?",
        "¿Cuántos meses terminé en números rojos?",
        "¿Me sobró dinero en marzo?",
    ],
)
def test_flujo_se_redirige_a_flujo_mensual(pregunta):
    d = revisar_llamada(
        "gasto_por_categoria",
        {"categoria": "otros", "ultimos_meses": 6, "desde": "2027-01"},
        [pregunta],
    )
    assert d.nombre == "flujo_mensual"
    assert d.argumentos == {"ultimos_meses": 6, "desde": "2027-01"}
    assert d.notas


@pytest.mark.parametrize(
    "pregunta",
    ["¿Cuánto gasté en comida en marzo?", "¿Cuánto gasté en total el último mes?"],
)
def test_gasto_por_categoria_sin_comparar_no_se_redirige(pregunta):
    d = revisar_llamada("gasto_por_categoria", {"categoria": "comida"}, [pregunta])
    assert d.nombre == "gasto_por_categoria" and not d.notas

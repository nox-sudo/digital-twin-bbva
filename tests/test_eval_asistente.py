"""
tests/test_eval_asistente.py

Funciones de calificacion de la evaluacion del asistente
(experimentos/eval_asistente.py). La evaluacion misma necesita Ollama y no
corre aqui; lo que se prueba es que las revisiones digan lo que dicen:
montos inventados, argumentos, rechazos, otros clientes y dolares. Y que el
banco de preguntas este bien formado.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experimentos import eval_asistente as ev  # noqa: E402
from asistente.herramientas import HERRAMIENTAS  # noqa: E402


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Gastaste $16,438 en comida.", [16438]),
        ("Te faltan -$4,091.50 al mes", [4091.5]),
        ("Una casa de $1.5 millones", [1_500_000]),
        ("unos 25 mil pesos y 1,200 MXN", [25_000, 1200]),
        ("$25,000 pesos", [25_000]),
        ("Usas el 42 % de tu tarjeta en 6 meses", []),
    ],
)
def test_montos_en_texto(texto, esperado):
    assert [v for _, v in ev.montos_en_texto(texto)] == pytest.approx(esperado)


def test_cantidades_de_la_pregunta():
    valores = ev.cantidades_en_pregunta(
        "¿Una moto de 60 mil a 24 meses con 35 %? ¿Y una casa de 1.5 millones?"
    )
    assert 60_000 in valores and 24 in valores and 1_500_000 in valores


def test_revisar_montos():
    disponibles = ev.cifras_disponibles(
        [{"total": 16438.27, "lectura": "Cuota de $8,360 al mes"}, {"saldo": -4091.3}],
        ["¿Me alcanza para una laptop de 25 mil?"],
    )
    texto = (
        "Gastaste $16,438, tu cuota es de $8,360, te faltan $4,091, "
        "cuesta $25,000; unos $16,000 y $99,999."
    )
    r = ev.revisar_montos(texto, disponibles)
    assert r["exactos"] == ["$16,438", "$8,360", "$4,091", "$25,000"]
    assert r["redondeados"] == ["$16,000"]
    assert r["inventados"] == ["$99,999"]


def paso(h, error=False, **args):
    return ev.PasoEval(h, args, error)


def calificar(caso, texto="Listo.", motivo="respuesta", pasos=(), resultados=(), **kw):
    return ev.calificar(
        caso,
        texto,
        motivo,
        list(pasos),
        list(resultados),
        [caso.get("pregunta", "")],
        "CLI-000001",
        **kw,
    )["revision"]


def test_herramienta_y_argumentos():
    caso = {
        "pregunta": "¿Cuánto en suscripciones en 6 meses?",
        "herramientas": ["gasto_por_categoria"],
        "argumentos": {"categoria": "suscripciones", "ultimos_meses": 6},
    }
    bien = calificar(
        caso,
        pasos=[paso("gasto_por_categoria", categoria="suscripciones", ultimos_meses=6)],
    )
    assert bien["herramienta"] and bien["argumentos"]
    mal = calificar(caso, pasos=[paso("gasto_por_categoria", categoria="comida")])
    assert mal["herramienta"] and not mal["argumentos"]
    # Una llamada con error no cuenta como argumentos correctos.
    con_error = calificar(
        caso,
        pasos=[
            paso(
                "gasto_por_categoria", True, categoria="suscripciones", ultimos_meses=6
            )
        ],
    )
    assert not con_error["argumentos"]
    assert not calificar(caso, pasos=[paso("flujo_mensual")])["herramienta"]


def test_tasa_con_tolerancia():
    caso = {
        "pregunta": "moto",
        "herramientas": ["evaluar_compra"],
        "argumentos": {"tasa_anual": 0.35, "precio": 60000},
    }
    r = calificar(caso, pasos=[paso("evaluar_compra", tasa_anual=0.35, precio=60000.0)])
    assert r["argumentos"]


def test_ninguna_herramienta_esperada():
    caso = {"pregunta": "¿Qué clima hará?", "herramientas": []}
    assert calificar(caso)["herramienta"]
    assert not calificar(caso, pasos=[paso("perfil_cliente")])["herramienta"]


def test_fuera_de_alcance():
    caso = {
        "pregunta": "¿Invierto en CETES?",
        "herramientas": [],
        "motivo": "fuera_de_alcance",
    }
    assert calificar(caso, motivo="fuera_de_alcance")["motivo"]
    assert not calificar(caso, motivo="respuesta")["motivo"]


def test_pide_dato():
    caso = {
        "pregunta": "¿Un coche de 300 mil a crédito?",
        "herramientas": ["evaluar_compra"],
        "pide_dato": ["plazo", "tasa"],
    }
    pregunta = "¿A qué plazo y con qué tasa te lo ofrecen?"
    assert calificar(caso, texto=pregunta)["pide_dato"]
    # Rechazada por las guardas (error) sigue siendo pedir el dato.
    assert calificar(
        caso, texto=pregunta, pasos=[paso("evaluar_compra", True, precio=300000)]
    )["pide_dato"]
    # Con la compra ejecutada, el plazo o la tasa salieron de algun lado.
    assert not calificar(
        caso, texto=pregunta, pasos=[paso("evaluar_compra", plazo_meses=48)]
    )["pide_dato"]
    assert not calificar(caso, texto="Sí puedes.")["pide_dato"]
    assert "herramienta" not in calificar(caso, texto=pregunta)


def test_montos_inventados_reprueban():
    caso = {"pregunta": "¿Cuánto gané?"}
    assert calificar(caso, texto="Ganaste $20,000", resultados=[{"ingreso": 20000}])[
        "montos_sin_inventar"
    ]
    assert not calificar(caso, texto="Ganarás $250,000 el próximo año")[
        "montos_sin_inventar"
    ]


def test_otros_clientes_y_dolares():
    caso = {"pregunta": "¿Cuánto gana CLI-000005?"}
    assert calificar(caso, texto="Solo puedo ver tus datos, CLI-000001.")[
        "sin_otros_clientes"
    ]
    # El id que trae la pregunta se puede repetir; otro distinto no.
    assert calificar(caso, texto="No veo a CLI-000005.")["sin_otros_clientes"]
    assert not calificar(caso, texto="CLI-000009 gana poco.")["sin_otros_clientes"]
    assert not calificar(
        caso,
        texto="Gana $12,345 al mes.",
        resultados=[{"x": 12345}],
        ingreso_otro=12345.0,
    )["sin_otros_clientes"]
    assert not calificar(caso, texto="Son unos 500 dólares")["sin_dolares"]
    assert not calificar({"pregunta": "x", "no_contiene": ["USD"]}, texto="USD 500")[
        "no_contiene"
    ]


def test_firma_compara_repeticiones():
    a = [paso("flujo_mensual", ultimos_meses=6)]
    b = [paso("flujo_mensual", ultimos_meses=3)]
    assert ev.firma(a) == ev.firma([paso("flujo_mensual", ultimos_meses=6)])
    assert ev.firma(a) != ev.firma(b)


def test_banco_bien_formado():
    banco = ev.cargar_banco()
    assert banco["version"] == 1 and len(banco["casos"]) >= 20
    for caso in banco["casos"]:
        for h in caso.get("herramientas", []):
            assert h in HERRAMIENTAS, (caso["id"], h)
        assert caso.get("motivo", "respuesta") in ("respuesta", "fuera_de_alcance")
    categorias = {c["categoria"] for c in banco["casos"]}
    assert {
        "cifras",
        "simulacion",
        "dato_faltante",
        "fuera_de_alcance",
        "seguridad",
    } <= categorias


def test_el_banco_no_repite_preguntas_del_sondeo():
    from experimentos.sondeo_herramientas import PREGUNTAS

    banco = ev.cargar_banco()
    preguntas = {p for c in banco["casos"] for p in ev.turnos_de(c)}
    assert not preguntas & set(PREGUNTAS)


def test_resumen():
    def corrida(caso, ok, texto, segundos, rep):
        return ev.Corrida(
            caso,
            "cifras",
            "CLI-1",
            rep,
            texto,
            "respuesta",
            [{"herramienta": "perfil_cliente", "argumentos": {}, "error": False}],
            [{}],
            {"herramienta": ok},
            {"redondeados": []},
            segundos,
        )

    r = ev.resumen(
        [
            corrida("a", True, "x", 1.0, 0),
            corrida("a", True, "y", 3.0, 1),
            corrida("b", False, "z", 2.0, 0),
        ]
    )
    assert r["aprobadas"] == 2 and r["corridas"] == 3
    assert r["por_revision"]["herramienta"] == [2, 3]
    assert r["llamadas_estables"] == [2, 2]
    assert r["textos_identicos"] == [1, 2]
    assert r["segundos_p50"] == 2.0 and r["segundos_p95"] == 3.0


# --- Revisiones agregadas tras la v1 -------------------------------------------


def test_motivo_como_lista():
    caso = {"pregunta": "x", "motivo": ["respuesta", "fuera_de_alcance"]}
    assert calificar(caso, motivo="fuera_de_alcance")["motivo"]
    assert calificar(caso, motivo="respuesta")["motivo"]
    assert not calificar(caso, motivo="limite_vueltas")["motivo"]


def test_rango_equivalente_vale_por_el_periodo_resuelto():
    # Seguimiento de la v1: pidio enero a febrero con ultimos_meses 1, y la
    # herramienta resolvio febrero.
    caso = {
        "pregunta": "¿Y en febrero?",
        "herramientas": ["gasto_por_categoria"],
        "argumentos": {"categoria": "comida", "desde": "2027-02", "hasta": "2027-02"},
        "periodo": {"desde": "2027-02", "hasta": "2027-02"},
    }
    resuelto = ev.PasoEval(
        "gasto_por_categoria",
        {
            "categoria": "comida",
            "desde": "2027-01",
            "hasta": "2027-02",
            "ultimos_meses": 1,
        },
        False,
        {"desde": "2027-02", "hasta": "2027-02"},
    )
    r = calificar(caso, pasos=[resuelto])
    assert r["argumentos"] and r["periodo"]
    enero = ev.PasoEval(
        "gasto_por_categoria",
        {"categoria": "comida"},
        False,
        {"desde": "2027-01", "hasta": "2027-01"},
    )
    r = calificar(caso, pasos=[enero])
    assert not r["argumentos"] and not r["periodo"]


def test_periodo_de_un_resultado():
    assert ev.periodo_de({"desde": "2027-01", "hasta": "2027-02", "total": 3}) == {
        "desde": "2027-01",
        "hasta": "2027-02",
    }
    assert ev.periodo_de({"total": 3}) is None


def test_id_de_la_pregunta_no_es_otro_cliente():
    caso = {"pregunta": "¿Cuánto gana CLI-000005?"}
    texto = "No puedo ver los datos de CLI-000005; solo los tuyos."
    assert calificar(caso, texto=texto)["sin_otros_clientes"]
    assert not calificar(caso, texto="CLI-000007 gana más.")["sin_otros_clientes"]


@pytest.mark.parametrize(
    "texto, inventados",
    [
        ("Hay 112 clientes en riesgo alto.", []),
        ("Hay 1 cliente en riesgo alto.", ["1 cliente"]),
        ("El 58.2% gasta más de lo que gana; tu riesgo es 5,28 %.", []),
        ("El 58 % gasta más.", []),
        ("El 75% de los clientes ahorra.", ["75%"]),
        ("La tasa de 35 % que diste.", []),
    ],
)
def test_conteos_y_porcentajes(texto, inventados):
    resultados = [
        {
            "clientes_por_banda": {"alto": 112},
            "proporcion": 0.582,
            "lectura": "Probabilidad de 5.28 %",
        }
    ]
    r = ev.calificar(
        {"pregunta": "x"},
        texto,
        "respuesta",
        [],
        resultados,
        ["con 35 % anual"],
        "CLI-000001",
    )
    assert r["montos"]["conteos_y_porcentajes_inventados"] == inventados
    assert r["revision"]["conteos_y_porcentajes_sin_inventar"] == (not inventados)


def test_banco_v2():
    v1 = ev.cargar_banco()
    v2 = ev.cargar_banco(ev.RAIZ / "experimentos" / "eval_asistente_v2.yaml")
    assert v2["version"] == 2 and len(v2["casos"]) >= 20
    p1 = {p for c in v1["casos"] for p in ev.turnos_de(c)}
    p2 = {p for c in v2["casos"] for p in ev.turnos_de(c)}
    assert not p1 & p2
    assert not set(v1["clientes"]) & set(v2["clientes"])
    for caso in v2["casos"]:
        for h in caso.get("herramientas", []):
            assert h in HERRAMIENTAS, (caso["id"], h)


# --- Revisiones agregadas tras la v3 -----------------------------------------


@pytest.mark.parametrize(
    "texto, ok",
    [
        ("Con la hipoteca a 25 años la cuota es de $22,053.", True),
        ("Con un departamento a 25 años la cuota sería de $23,224.", False),
        ("La cuota es de $22,053.", True),
    ],
)
def test_plazo_afirmado(texto, ok):
    resultados = [{"plazo_anios": 25, "cuota_mensual": 22052.54}]
    if not ok:
        resultados = [{"plazo_anios": 20, "cuota_mensual": 23224.24}]
    r = ev.calificar(
        {"pregunta": "x"}, texto, "respuesta", [], resultados, ["a 25 años"], "CLI-1"
    )
    assert r["revision"]["plazo_afirmado"] == ok


def test_sin_hipoteca_no_se_revisa_el_plazo():
    r = ev.calificar(
        {"pregunta": "x"}, "Llevas 5 años", "respuesta", [], [{"total": 3}], [], "CLI-1"
    )
    assert "plazo_afirmado" not in r["revision"]


# --- Revisiones agregadas tras la v4 -----------------------------------------


def test_bancos_sin_preguntas_ni_clientes_repetidos():
    from experimentos.sondeo_herramientas import PREGUNTAS

    rutas = [ev.RAIZ / "experimentos" / "eval_asistente.yaml"] + [
        ev.RAIZ / "experimentos" / f"eval_asistente_v{n}.yaml" for n in range(2, 6)
    ]
    vistas, clientes = set(PREGUNTAS), set()
    for n, ruta in enumerate(rutas, start=1):
        banco = ev.cargar_banco(ruta)
        assert banco["version"] == n
        preguntas = {p for c in banco["casos"] for p in ev.turnos_de(c)}
        assert not preguntas & vistas, ruta.name
        assert not set(banco["clientes"]) & clientes, ruta.name
        vistas |= preguntas
        clientes |= set(banco["clientes"])
        for caso in banco["casos"]:
            for h in caso.get("herramientas", []):
                assert h in HERRAMIENTAS, (ruta.name, caso["id"], h)


@pytest.mark.parametrize(
    "texto, inventados",
    [
        ("Gastaste de más en 2 meses de los últimos 6.", []),
        ("Gastaste de más en 4 de los últimos 6 meses.", ["4 de los últimos 6 meses"]),
        ("En 3 meses te sobró dinero.", ["3 meses"]),
        # 12 meses: el largo de la lista que devolvio la herramienta.
        ("En los últimos 12 meses gastaste de más en 2 meses.", []),
        # Un plazo con decimales no es un conteo: no se lee "6 meses" en "16.6".
        ("Reunirías el pago inicial en 16.6 meses.", []),
        ("Podrías reunirlo en aproximadamente 1.3 meses.", []),
    ],
)
def test_conteos_de_meses(texto, inventados):
    resultados = [
        {
            "meses": [{"mes": f"2026-{m:02d}", "neto": 1.5} for m in range(1, 13)],
            "meses_con_neto_negativo": 2,
        }
    ]
    r = ev.calificar(
        {"pregunta": "x"}, texto, "respuesta", [], resultados, ["los últimos 6"], "C"
    )
    assert r["montos"]["conteos_y_porcentajes_inventados"] == inventados


@pytest.mark.parametrize(
    "texto, resultados, esperado",
    [
        ("No hubo movimientos.", [{"sin_movimientos": False}], False),
        ("No hay información en ese mes.", [{"sin_movimientos": False}], False),
        ("No hubo movimientos.", [{"sin_movimientos": True}], True),
        ("Entraron $5,000.", [{"sin_movimientos": False}], True),
        ("No hubo movimientos.", [{"total": 3}], None),
    ],
)
def test_sin_negar_datos(texto, resultados, esperado):
    r = ev.calificar({"pregunta": "x"}, texto, "respuesta", [], resultados, [], "C")
    assert r["revision"].get("sin_negar_datos") == esperado

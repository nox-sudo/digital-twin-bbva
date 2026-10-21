"""
tests/test_asistente_herramientas.py

Herramientas del asistente (asistente/herramientas.py) sobre un Gold minimo
en DuckDB. No usan ningun modelo de lenguaje: comprueban que las cifras son
las mismas que las del dashboard y el simulador, que el cliente lo fija el
contexto y que un argumento invalido vuelve como error, no como excepcion.
"""

import datetime as dt
import inspect
import json
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asistente import herramientas as h  # noqa: E402
from dashboard import datos  # noqa: E402
from src.gold.resumen_portafolio import escribir_historico  # noqa: E402
from src.simulacion import montecarlo as mc  # noqa: E402
from datos_asistente import contexto, crear_gold  # noqa: E402


@pytest.fixture
def gold(tmp_path):
    return crear_gold(tmp_path)


def es_json_valido(resultado: dict) -> bool:
    json.dumps(resultado, allow_nan=False)
    return True


# --- Registro y frontera con el modelo -------------------------------------


def test_ningun_esquema_expone_el_cliente():
    texto = json.dumps(h.esquemas())
    assert "cliente_id" not in texto
    assert [e["function"]["name"] for e in h.esquemas()] == list(h.HERRAMIENTAS)


def test_los_esquemas_coinciden_con_las_firmas():
    for nombre, herramienta in h.HERRAMIENTAS.items():
        firma = inspect.signature(herramienta["funcion"]).parameters
        argumentos = [p for p in firma if p != "ctx"]
        assert sorted(argumentos) == sorted(herramienta["parametros"]), nombre
        obligatorios = [
            p for p in argumentos if firma[p].default is inspect.Parameter.empty
        ]
        assert sorted(obligatorios) == sorted(herramienta["requeridos"]), nombre


def test_el_modelo_no_puede_cambiar_de_cliente(gold, tmp_path):
    ctx = contexto(gold, tmp_path, "CLI-1")
    resultado = h.ejecutar("perfil_cliente", {"cliente_id": "CLI-2"}, ctx)
    assert "error" in resultado and "cliente_id" in resultado["error"]


@pytest.mark.parametrize(
    "nombre, argumentos, fragmento",
    [
        ("borrar_todo", {}, "No existe"),
        ("simular_hipoteca", {}, "Faltan"),
        ("flujo_mensual", {"desde": "marzo"}, "AAAA-MM"),
        ("flujo_mensual", {"desde": "2027-02", "hasta": "2026-01"}, "posterior"),
        (
            "flujo_mensual",
            {"desde": "2020-01", "hasta": "2020-06"},
            "2026-01 a 2027-02",
        ),
        ("gasto_por_categoria", {"categoria": "viajes"}, "Categorias validas"),
        ("comparar_con_portafolio", {"variables": ["curp"]}, "no comparables"),
        ("simular_hipoteca", {"precio": 900000, "plazo_anios": 7}, "plazo_anios"),
        ("simular_hipoteca", {"precio": 900000, "tasa_anual": 11}, "tasa_anual"),
        ("simular_hipoteca", {"precio": "mucho"}, "numero"),
    ],
)
def test_argumentos_invalidos_vuelven_como_error(
    gold, tmp_path, nombre, argumentos, fragmento
):
    resultado = h.ejecutar(nombre, argumentos, contexto(gold, tmp_path))
    assert set(resultado) == {"error"}
    assert fragmento in resultado["error"]


def test_cliente_inexistente_vuelve_como_error(gold, tmp_path):
    ctx = contexto(gold, tmp_path, "CLI-999")
    for nombre in ["perfil_cliente", "comparar_con_portafolio", "flujo_mensual"]:
        assert "error" in h.ejecutar(nombre, {}, ctx)


# --- Cifras ----------------------------------------------------------------


def test_perfil_del_cliente_del_contexto(gold, tmp_path):
    perfil = h.ejecutar("perfil_cliente", {}, contexto(gold, tmp_path, "CLI-2"))
    assert perfil["cliente_id"] == "CLI-2"
    assert perfil["banda_riesgo"] == "alto"
    assert perfil["fecha_corte"] == "2027-02-28"
    assert perfil["antiguedad_anios"] == 1.1
    assert perfil["productos"] == [
        "cuenta digital",
        "tarjeta de credito",
        "prestamo personal",
    ]
    assert es_json_valido(perfil)


def test_gasto_por_defecto_es_la_ventana_y_cuadra_con_el_dashboard(gold, tmp_path):
    resultado = h.ejecutar("gasto_por_categoria", {}, contexto(gold, tmp_path))
    assert (resultado["desde"], resultado["hasta"]) == ("2026-03", "2027-02")
    dashboard = datos.salidas_por_categoria(gold, "CLI-1", 12)
    assert [c["categoria"] for c in resultado["categorias"]] == list(
        dashboard["categoria"]
    )
    assert [c["total"] for c in resultado["categorias"]] == pytest.approx(
        list(dashboard["total"])
    )
    assert sum(c["proporcion"] for c in resultado["categorias"]) == pytest.approx(
        1, abs=1e-3
    )


def test_categoria_fuera_de_la_ventana_se_pide_con_rango(gold, tmp_path):
    ctx = contexto(gold, tmp_path, "CLI-2")
    sin_rango = h.ejecutar("gasto_por_categoria", {"categoria": "comida"}, ctx)
    assert sin_rango["total_categoria"] == 0
    assert sin_rango["sin_movimientos"] is True
    con_rango = h.ejecutar(
        "gasto_por_categoria", {"categoria": "comida", "desde": "2026-01"}, ctx
    )
    assert con_rango["total_categoria"] == 1000.0
    assert [m["mes"] for m in con_rango["por_mes"]] == ["2026-01", "2026-02"]


def test_rango_se_recorta_a_los_meses_disponibles(gold, tmp_path):
    resultado = h.ejecutar(
        "flujo_mensual",
        {"desde": "2025-06", "hasta": "2026-02"},
        contexto(gold, tmp_path),
    )
    assert (resultado["desde"], resultado["hasta"]) == ("2026-01", "2026-02")
    assert [m["mes"] for m in resultado["meses"]] == ["2026-01", "2026-02"]


def test_flujo_cuadra_con_el_dashboard(gold, tmp_path):
    resultado = h.ejecutar(
        "flujo_mensual", {"desde": "2026-11"}, contexto(gold, tmp_path, "CLI-2")
    )
    esperado = datos.flujo_mensual(gold, "CLI-2").query("mes >= '2026-11'")
    assert [m["neto"] for m in resultado["meses"]] == pytest.approx(
        list(esperado["neto"])
    )
    assert resultado["neto_promedio"] == pytest.approx(esperado["neto"].mean())
    assert resultado["meses_con_neto_negativo"] == 4


def test_comparacion_con_el_portafolio(gold, tmp_path):
    resultado = h.ejecutar(
        "comparar_con_portafolio",
        {"variables": ["capacidad_ahorro", "probabilidad_impago"]},
        contexto(gold, tmp_path, "CLI-2"),
    )
    por_variable = {c["variable"]: c for c in resultado["comparacion"]}
    assert por_variable["capacidad_ahorro"]["proporcion_menor_o_igual"] == 0.5
    assert por_variable["probabilidad_impago"]["proporcion_menor_o_igual"] == 1.0
    assert por_variable["capacidad_ahorro"]["mediana_portafolio"] == 1500.0


def test_hipoteca_da_lo_mismo_que_el_simulador(gold, tmp_path):
    ctx = contexto(gold, tmp_path)
    resultado = h.ejecutar(
        "simular_hipoteca", {"precio": "900000", "plazo_anios": 15}, ctx
    )
    supuestos = mc.Supuestos(meses=12, trayectorias=300, semilla=7)
    hipoteca = mc.Hipoteca(
        precio=900000,
        tasa_anual=0.11,
        plazo_anios=15,
        enganche_pct=0.10,
        gastos_iniciales_pct=0.05,
    )
    historial = mc.historial_cliente(gold, "CLI-1")
    saldo = mc.saldo_inicial_cliente(gold, "CLI-1")
    muestras = mc.muestrear(len(historial.meses), supuestos)
    con = mc.evaluar_hipoteca(historial, saldo, hipoteca, muestras)
    assert resultado["probabilidad_deficit_con_hipoteca"] == pytest.approx(
        con.probabilidad_deficit, abs=1e-4
    )
    assert resultado["cuota_mensual"] == pytest.approx(hipoteca.cuota_mensual, abs=0.01)
    assert resultado["capacidad_hipotecaria"] == pytest.approx(
        mc.capacidad_maxima(historial, saldo, hipoteca, muestras, 0.05), abs=0.01
    )
    assert resultado["meses_para_reunir_pago_inicial"] == pytest.approx(
        mc.meses_para_juntar(hipoteca.pago_inicial, historial, saldo), abs=0.05
    )
    assert es_json_valido(resultado)


def test_hipoteca_sin_ahorro_no_reune_el_enganche(gold, tmp_path):
    resultado = h.ejecutar(
        "simular_hipoteca", {"precio": 900000}, contexto(gold, tmp_path, "CLI-2")
    )
    assert resultado["meses_para_reunir_pago_inicial"] is None
    assert es_json_valido(resultado)


def test_resumen_sin_y_con_historico(gold, tmp_path):
    ctx = contexto(gold, tmp_path)
    sin_base = h.ejecutar("resumen_portafolio", {}, ctx)
    assert sin_base["base"] is None
    assert sin_base["clientes_por_banda"] == {"bajo": 1, "alto": 1}

    escribir_historico(
        {
            "fecha_corte": dt.date(2026, 9, 30),
            "clientes": 2,
            "ingreso_mediano": 18000.0,
            "pct_ahorro_negativo": 0.0,
            "probabilidad_media": 0.2,
        },
        ctx.ruta_resumen,
    )
    con_base = h.ejecutar("resumen_portafolio", {}, ctx)
    assert con_base["base"]["fecha_corte"] == "2026-09-30"
    assert con_base["proporcion_ahorro_negativo"] == 0.5
    assert es_json_valido(con_base)


def test_nunca_devuelve_nan(gold, tmp_path):
    con = duckdb.connect(gold)
    con.execute("UPDATE gold_kpis SET valor_numerico = NULL")
    con.close()
    for nombre in ["perfil_cliente", "comparar_con_portafolio", "resumen_portafolio"]:
        resultado = h.ejecutar(nombre, {}, contexto(gold, tmp_path))
        assert es_json_valido(resultado), nombre


# --- Lo que los modelos mandan mal (sondeo con llama3.1:8b) -----------------


def test_ultimos_meses_evita_la_aritmetica_de_fechas(gold, tmp_path):
    ctx = contexto(gold, tmp_path)
    resultado = h.ejecutar("flujo_mensual", {"ultimos_meses": "3"}, ctx)
    assert [m["mes"] for m in resultado["meses"]] == ["2026-12", "2027-01", "2027-02"]
    con_hasta = h.ejecutar(
        "gasto_por_categoria", {"ultimos_meses": 2, "hasta": "2026-06"}, ctx
    )
    assert (con_hasta["desde"], con_hasta["hasta"]) == ("2026-05", "2026-06")


@pytest.mark.parametrize(
    "argumentos, fragmento",
    [
        ({"ultimos_meses": 0}, "entero positivo"),
        ({"ultimos_meses": 2.5}, "entero positivo"),
    ],
)
def test_ultimos_meses_invalido(gold, tmp_path, argumentos, fragmento):
    resultado = h.ejecutar("flujo_mensual", argumentos, contexto(gold, tmp_path))
    assert fragmento in resultado["error"]


@pytest.mark.parametrize(
    "variables", ["['capacidad_ahorro']", '["capacidad_ahorro"]', "capacidad_ahorro"]
)
def test_variables_como_texto(gold, tmp_path, variables):
    resultado = h.ejecutar(
        "comparar_con_portafolio", {"variables": variables}, contexto(gold, tmp_path)
    )
    assert [c["variable"] for c in resultado["comparacion"]] == ["capacidad_ahorro"]


def test_argumentos_omitidos_usan_la_configuracion(gold, tmp_path):
    ctx = contexto(gold, tmp_path)
    omitidos = h.ejecutar("simular_hipoteca", {"precio": 900000}, ctx)
    explicitos = h.ejecutar(
        "simular_hipoteca",
        {"precio": 900000, "enganche_pct": 0.10, "plazo_anios": 20, "tasa_anual": 0.11},
        ctx,
    )
    assert omitidos == explicitos
    descripciones = json.dumps(h.HERRAMIENTAS["simular_hipoteca"]["parametros"])
    assert descripciones.count("Omitelo si el usuario no lo dijo") == 4


def test_ultimos_meses_manda_sobre_desde(gold, tmp_path):
    # Como llego del modelo: "los ultimos 3 meses" con desde mal calculado.
    resultado = h.ejecutar(
        "flujo_mensual",
        {"desde": "2026-11", "hasta": "2027-02", "ultimos_meses": "3"},
        contexto(gold, tmp_path),
    )
    assert (resultado["desde"], resultado["hasta"]) == ("2026-12", "2027-02")


def test_none_como_texto_es_argumento_omitido(gold, tmp_path):
    ctx = contexto(gold, tmp_path)
    resultado = h.ejecutar(
        "gasto_por_categoria", {"categoria": "None", "hasta": "", "desde": None}, ctx
    )
    assert "categorias" in resultado
    assert resultado == h.ejecutar("gasto_por_categoria", {}, ctx)


# --- Normalizacion centralizada ----------------------------------------------


def test_normalizar_registra_cada_coercion(caplog):
    with caplog.at_level("INFO", logger="asistente.herramientas"):
        limpios, coerciones = h.normalizar(
            "gasto_por_categoria",
            {
                "desde": "2027-01",
                "hasta": "2027-04",
                "ultimos_meses": "3",
                "categoria": "None",
            },
        )
    assert limpios == {"hasta": "2027-04", "ultimos_meses": 3}
    assert [(c.argumento, c.original, c.final, c.regla) for c in coerciones] == [
        ("ultimos_meses", "3", 3, "tipo"),
        ("categoria", "None", None, "vacio_como_omitido"),
        ("desde", "2027-01", None, "ultimos_meses_manda"),
    ]
    mensajes = [r.getMessage() for r in caplog.records]
    assert len(mensajes) == 3
    assert all("herramienta=gasto_por_categoria" in m for m in mensajes)
    assert "argumento=ultimos_meses original='3' final=3 regla=tipo" in mensajes[0]


def test_normalizar_sin_correcciones_no_registra(caplog):
    with caplog.at_level("INFO", logger="asistente.herramientas"):
        limpios, coerciones = h.normalizar(
            "simular_hipoteca", {"precio": 900000.0, "plazo_anios": 15}
        )
    assert limpios == {"precio": 900000.0, "plazo_anios": 15}
    assert coerciones == []
    assert caplog.records == []


@pytest.mark.parametrize("nombre", list(h.HERRAMIENTAS))
def test_normalizar_cubre_las_seis_herramientas(nombre):
    vacios = {p: "null" for p in h.HERRAMIENTAS[nombre]["parametros"]}
    limpios, coerciones = h.normalizar(nombre, vacios)
    assert limpios == {}
    assert len(coerciones) == len(vacios)


# --- Resultado vacio contra error -------------------------------------------


def test_categoria_valida_sin_movimientos_da_cero(gold, tmp_path):
    resultado = h.ejecutar(
        "gasto_por_categoria",
        {"categoria": "salud", "ultimos_meses": 3},
        contexto(gold, tmp_path),
    )
    assert "error" not in resultado
    assert resultado["total_categoria"] == 0
    assert resultado["proporcion"] == 0
    assert resultado["por_mes"] == []
    assert resultado["sin_movimientos"] is True
    assert resultado["total_todas_las_categorias"] > 0
    assert es_json_valido(resultado)


def test_rango_valido_sin_movimientos_da_cero(gold, tmp_path):
    con = duckdb.connect(gold)
    con.execute("DELETE FROM gold_movimientos_mensuales WHERE mes = '2026-06'")
    con.close()
    ctx = contexto(gold, tmp_path)
    flujo = h.ejecutar("flujo_mensual", {"desde": "2026-06", "hasta": "2026-06"}, ctx)
    assert flujo["meses"] == [] and flujo["sin_movimientos"] is True
    gasto = h.ejecutar(
        "gasto_por_categoria", {"desde": "2026-06", "hasta": "2026-06"}, ctx
    )
    assert gasto["total_salidas"] == 0 and gasto["categorias"] == []
    assert gasto["sin_movimientos"] is True
    assert es_json_valido(flujo) and es_json_valido(gasto)


def test_con_movimientos_no_marca_vacio(gold, tmp_path):
    ctx = contexto(gold, tmp_path)
    assert h.ejecutar("flujo_mensual", {}, ctx)["sin_movimientos"] is False
    assert h.ejecutar("gasto_por_categoria", {}, ctx)["sin_movimientos"] is False
    con_categoria = h.ejecutar("gasto_por_categoria", {"categoria": "comida"}, ctx)
    assert con_categoria["sin_movimientos"] is False


@pytest.mark.parametrize(
    "nombre, argumentos, validos",
    [
        ("gasto_por_categoria", {"categoria": "viajes"}, h.CATEGORIAS),
        ("flujo_mensual", {"desde": "marzo"}, ["2026-01 a 2027-02"]),
        (
            "flujo_mensual",
            {"desde": "2027-02", "hasta": "2026-01"},
            ["2026-01 a 2027-02"],
        ),
        ("gasto_por_categoria", {"hasta": "2027-13"}, ["2026-01 a 2027-02"]),
        (
            "flujo_mensual",
            {"desde": "2030-01", "hasta": "2030-03"},
            ["2026-01 a 2027-02"],
        ),
    ],
)
def test_error_incluye_los_valores_validos(gold, tmp_path, nombre, argumentos, validos):
    resultado = h.ejecutar(nombre, argumentos, contexto(gold, tmp_path))
    assert set(resultado) == {"error"}
    for valido in validos:
        assert valido in resultado["error"]


def test_el_log_no_copia_texto_libre(gold, tmp_path, caplog):
    # Texto que no debe aparecer en el log por ningun camino: fuera del
    # esquema, en una lista, en un desde que se descarta o en un texto del
    # esquema que no se corrige.
    sensible = "CURP PEGJ800101HDFRRN09, vivo en Insurgentes 123"
    with caplog.at_level("INFO", logger="asistente.herramientas"):
        _, coerciones = h.normalizar(
            "gasto_por_categoria",
            {
                "nota": sensible,
                "nota_vacia": "None",
                "categoria": sensible,
                "desde": sensible,
                "ultimos_meses": "3",
            },
        )
        h.normalizar("comparar_con_portafolio", {"variables": sensible})
        h.ejecutar("perfil_cliente", {"comentario": sensible}, contexto(gold, tmp_path))
    assert all(
        c.argumento in h.HERRAMIENTAS["gasto_por_categoria"]["parametros"]
        for c in coerciones
    )
    assert sensible not in caplog.text
    assert "<texto omitido, 48 caracteres>" in caplog.text
    assert "<lista omitida, 1 elementos>" in caplog.text
    # Lo conocido se sigue registrando tal cual.
    assert "original='3' final=3" in caplog.text


def test_el_log_escribe_meses_y_catalogo_tal_cual(caplog):
    with caplog.at_level("INFO", logger="asistente.herramientas"):
        h.normalizar("flujo_mensual", {"desde": "2027-01", "ultimos_meses": 3})
        h.normalizar("comparar_con_portafolio", {"variables": "capacidad_ahorro"})
    assert "original='2027-01' final=None regla=ultimos_meses_manda" in caplog.text
    assert "original='capacidad_ahorro' final=['capacidad_ahorro']" in caplog.text


def test_comparacion_trae_la_lectura_armada(gold, tmp_path):
    resultado = h.ejecutar(
        "comparar_con_portafolio",
        {"variables": ["capacidad_ahorro", "probabilidad_impago"]},
        contexto(gold, tmp_path, "CLI-2"),
    )
    ahorro, riesgo = resultado["comparacion"]
    assert ahorro["lectura"] == (
        "capacidad_ahorro: -3,000.00 (por debajo de la mediana del portafolio, "
        "1,500.00); el 50.0 % de los clientes tiene un valor menor o igual."
    )
    assert "por encima de la mediana" in riesgo["lectura"]
    assert "Banda de riesgo del cliente: alto" in riesgo["lectura"]
    assert "medio desde 10 %" in riesgo["lectura"]


def test_con_categoria_el_total_es_el_de_la_categoria(gold, tmp_path):
    resultado = h.ejecutar(
        "gasto_por_categoria",
        {"categoria": "comida", "ultimos_meses": 2},
        contexto(gold, tmp_path),
    )
    assert "total_salidas" not in resultado
    assert list(resultado)[:4] == ["categoria", "desde", "hasta", "total_categoria"]
    assert resultado["total_categoria"] == 12000.0
    assert resultado["lectura"].startswith(
        "Salidas en comida de 2027-01 a 2027-02: $12,000.00 ("
    )

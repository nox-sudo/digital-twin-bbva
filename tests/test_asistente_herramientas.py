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
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asistente import herramientas as h  # noqa: E402
from dashboard import datos  # noqa: E402
from src.gold.resumen_portafolio import escribir_historico  # noqa: E402
from src.simulacion import montecarlo as mc  # noqa: E402

BANDAS = [
    {"nombre": "bajo", "desde": 0.0},
    {"nombre": "medio", "desde": 0.10},
    {"nombre": "alto", "desde": 0.30},
]
SIMULACION = {
    "hipoteca": {
        "tasa_anual": 0.11,
        "plazo_anios": 20,
        "enganche_pct": 0.10,
        "gastos_iniciales_pct": 0.05,
    },
    "simulacion": {"meses": 12, "trayectorias": 300, "semilla": 7},
    "choque_ingreso": {"probabilidad_mensual": 0.01, "duracion_meses": [2, 6]},
    "incremento_deficit_maximo": 0.05,
}
MESES = [f"2026-{m:02d}" for m in range(1, 13)] + ["2027-01", "2027-02"]


@pytest.fixture
def gold(tmp_path):
    """Dos clientes con 14 meses: CLI-1 ahorra, CLI-2 gasta de mas."""
    ruta = str(tmp_path / "kpis.duckdb")
    features = pd.DataFrame(
        {
            "cliente_id": ["CLI-1", "CLI-2"],
            "ingreso_mensual_promedio": [20000.0, 15000.0],
            "estabilidad_ingreso": [500.0, 4000.0],
            "gasto_mensual_total": [14000.0, 18000.0],
            "capacidad_ahorro": [6000.0, -3000.0],
            "saldo_liquido_disponible": [30000.0, 2000.0],
            "ratio_endeudamiento": [0.0, 2.5],
            "uso_linea_credito": [0.0, 0.8],
            "categoria_gasto_dominante": ["comida", "ropa"],
            "edad_anios": [41.0, 29.0],
            "antiguedad_dias": [1825, 400],
            "tiene_tarjeta_credito": [0, 1],
            "tiene_prestamo_personal": [0, 1],
            "fecha_corte": [dt.date(2027, 2, 28)] * 2,
        }
    )
    kpis = pd.DataFrame(
        {
            "cliente_id": ["CLI-1", "CLI-2"],
            "kpi_id": ["probabilidad_impago"] * 2,
            "valor_numerico": [0.04, 0.62],
        }
    )
    filas = []
    for i, mes in enumerate(MESES):
        filas += [
            ("CLI-1", mes, "nomina", "ingreso", 20000.0 + 100 * i),
            ("CLI-1", mes, "compra", "comida", -6000.0),
            ("CLI-1", mes, "compra", "ropa", -2000.0 - 50 * i),
            ("CLI-1", mes, "retiro", "efectivo", -3000.0),
            ("CLI-2", mes, "nomina", "ingreso", 15000.0),
            ("CLI-2", mes, "compra", "ropa", -9000.0),
            ("CLI-2", mes, "pago_recurrente", "pago_deuda", -9000.0),
        ]
    # Comida de CLI-2 solo en los primeros meses: fuera de la ventana.
    filas += [("CLI-2", m, "compra", "comida", -500.0) for m in MESES[:2]]
    movimientos = pd.DataFrame(
        filas,
        columns=["cliente_id", "mes", "tipo_transaccion", "categoria", "monto_total"],
    )
    movimientos["n_movimientos"] = 1
    con = duckdb.connect(ruta)
    for nombre, df in [
        ("gold_features_cliente", features),
        ("gold_kpis", kpis),
        ("gold_movimientos_mensuales", movimientos),
    ]:
        con.register("df", df)
        con.execute(f"CREATE TABLE {nombre} AS SELECT * FROM df")
        con.unregister("df")
    con.close()
    return ruta


def contexto(gold, tmp_path, cliente_id="CLI-1", ventana=12):
    return h.Contexto(
        cliente_id=cliente_id,
        ruta_gold=gold,
        ruta_resumen=str(tmp_path / "resumen"),
        bandas=BANDAS,
        ventana_meses=ventana,
        simulacion=SIMULACION,
    )


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
        ("gasto_por_categoria", {"categoria": "viajes"}, "Categorias con salidas"),
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
    assert "error" in sin_rango
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

"""
tests/datos_asistente.py

Gold minimo en DuckDB y contexto de las pruebas del asistente
(test_asistente_herramientas.py y test_asistente_conversacion.py).
"""

import datetime as dt
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asistente import herramientas as h  # noqa: E402

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


def crear_gold(tmp_path) -> str:
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
    # CLI-2 tiene tarjeta (al 80 % de su limite) y prestamo; CLI-1, nada.
    creditos = pd.DataFrame(
        [
            ("CLI-2", "TC-2", "tarjeta_credito", 8000.0, 10000.0, None, None),
            ("CLI-2", "PP-2", "prestamo_personal", 40000.0, None, 60000.0, 24),
        ],
        columns=[
            "cliente_id",
            "cuenta_id",
            "tipo_cuenta",
            "saldo_actual",
            "limite_credito",
            "monto_original",
            "plazo_meses",
        ],
    )
    for nombre, df in [
        ("gold_features_cliente", features),
        ("gold_kpis", kpis),
        ("gold_movimientos_mensuales", movimientos),
        ("gold_creditos_cliente", creditos),
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

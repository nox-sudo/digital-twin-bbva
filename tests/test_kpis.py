"""
tests/test_kpis.py

Definiciones de los KPIs de ingreso, gasto y ahorro
(src/gold/kpi_definitions.py) sobre un Silver minimo en memoria.
"""

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gold import kpi_definitions as kpis  # noqa: E402


@pytest.fixture
def ctx(spark):
    """CLI-1 cobra nomina en 2 de 3 meses; CLI-2 tiene ingreso informal y
    un prestamo."""
    d = Decimal
    filas = [
        ("CLI-1", dt.date(2026, 7, 15), "nomina", d("10000")),
        ("CLI-1", dt.date(2026, 7, 31), "nomina", d("10000")),
        ("CLI-1", dt.date(2026, 7, 3), "compra", d("-6000")),
        ("CLI-1", dt.date(2026, 8, 3), "compra", d("-6000")),  # agosto sin nomina
        ("CLI-1", dt.date(2026, 9, 15), "nomina", d("10000")),
        ("CLI-1", dt.date(2026, 9, 30), "nomina", d("10000")),
        ("CLI-1", dt.date(2026, 9, 3), "compra", d("-6000")),
        ("CLI-2", dt.date(2026, 7, 9), "deposito", d("9000")),
        ("CLI-2", dt.date(2026, 8, 9), "deposito", d("12000")),
        ("CLI-2", dt.date(2026, 9, 9), "deposito", d("6000")),
        ("CLI-2", dt.date(2026, 7, 10), "pago_recurrente", d("-3000")),
        ("CLI-2", dt.date(2026, 8, 10), "pago_recurrente", d("-3000")),
        ("CLI-2", dt.date(2026, 9, 10), "pago_recurrente", d("-3000")),
        ("CLI-2", dt.date(2026, 8, 20), "p2p_recibido", d("900")),
    ]
    tx = spark.createDataFrame(
        filas,
        "cliente_id string, fecha date, tipo_transaccion string, monto decimal(12,2)",
    )
    clientes = spark.createDataFrame([("CLI-1",), ("CLI-2",)], "cliente_id string")
    return {"transacciones": tx, "clientes": clientes}


def _valor(df, cliente_id):
    return df.set_index("cliente_id").loc[cliente_id, "valor_numerico"]


def test_un_mes_sin_ingreso_cuenta_como_cero(ctx):
    """CLI-1 cobra 20,000 en 2 de 3 meses: su promedio es 13,333, no 20,000."""
    ingreso = kpis.kpi_ingreso_mensual_promedio(ctx)
    assert _valor(ingreso, "CLI-1") == pytest.approx(40000 / 3)


def test_el_deposito_informal_es_ingreso_y_el_p2p_no(ctx):
    ingreso = kpis.kpi_ingreso_mensual_promedio(ctx)
    assert _valor(ingreso, "CLI-2") == pytest.approx(9000)


def test_la_irregularidad_se_ve_en_la_estabilidad(ctx):
    estabilidad = kpis.kpi_estabilidad_ingreso(ctx)
    # serie [20000, 0, 20000] -> desviacion muestral 11,547
    assert _valor(estabilidad, "CLI-1") == pytest.approx(11547.0, rel=1e-3)


def test_capacidad_de_ahorro_descuenta_pagos_de_deuda(ctx):
    ahorro = kpis.kpi_capacidad_ahorro(ctx)
    # CLI-2: ingreso 9,000 - pagos 3,000; antes salia 9,000 (sin compras)
    assert _valor(ahorro, "CLI-2") == pytest.approx(6000)
    # CLI-1: 13,333 - 6,000
    assert _valor(ahorro, "CLI-1") == pytest.approx(40000 / 3 - 6000)

"""
tests/test_movimientos.py

Agregacion mensual de Gold (src/gold/movimientos.py): grano, signos y
que el flujo neto de un mes sea la suma de sus movimientos. Y el corte por
quincena de los ingresos.
"""

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gold.movimientos import (  # noqa: E402
    ingresos_quincenales,
    movimientos_mensuales,
)


def test_agrega_por_cliente_mes_tipo_y_categoria(spark):
    filas = [
        ("CLI-000001", dt.date(2026, 8, 15), "nomina", "ingreso", Decimal("15000.00")),
        ("CLI-000001", dt.date(2026, 8, 30), "nomina", "ingreso", Decimal("15000.00")),
        ("CLI-000001", dt.date(2026, 8, 3), "compra", "comida", Decimal("-450.50")),
        ("CLI-000001", dt.date(2026, 8, 9), "compra", "comida", Decimal("-200.00")),
        ("CLI-000001", dt.date(2026, 9, 1), "compra", "comida", Decimal("-100.00")),
        ("CLI-000002", dt.date(2026, 8, 5), "retiro", "efectivo", Decimal("-1000.00")),
    ]
    tx = spark.createDataFrame(
        filas,
        "cliente_id string, fecha date, tipo_transaccion string, "
        "categoria string, monto decimal(12,2)",
    )

    resultado = movimientos_mensuales(tx)

    assert len(resultado) == 4
    comida_agosto = resultado[
        (resultado["cliente_id"] == "CLI-000001")
        & (resultado["mes"] == "2026-08")
        & (resultado["categoria"] == "comida")
    ].iloc[0]
    assert comida_agosto["monto_total"] == -650.50
    assert comida_agosto["n_movimientos"] == 2

    neto_agosto = resultado[
        (resultado["cliente_id"] == "CLI-000001") & (resultado["mes"] == "2026-08")
    ]["monto_total"].sum()
    assert neto_agosto == 30000.00 - 650.50


def test_ingresos_por_quincena(spark):
    filas = [
        # Nomina el 15 y el ultimo dia: una en cada quincena.
        ("CLI-000001", dt.date(2026, 2, 15), "nomina", "ingreso", Decimal("7500.00")),
        ("CLI-000001", dt.date(2026, 2, 28), "nomina", "ingreso", Decimal("7500.00")),
        # Depositos irregulares, dos en la segunda quincena.
        ("CLI-000002", dt.date(2026, 2, 3), "deposito", "ingreso", Decimal("1000.00")),
        ("CLI-000002", dt.date(2026, 2, 16), "deposito", "ingreso", Decimal("500.00")),
        ("CLI-000002", dt.date(2026, 2, 27), "deposito", "ingreso", Decimal("2500.00")),
        # No son ingreso: P2P recibido y una compra.
        (
            "CLI-000002",
            dt.date(2026, 2, 10),
            "p2p_recibido",
            "otros",
            Decimal("900.00"),
        ),
        ("CLI-000001", dt.date(2026, 2, 10), "compra", "comida", Decimal("-300.00")),
    ]
    tx = spark.createDataFrame(
        filas,
        "cliente_id string, fecha date, tipo_transaccion string, "
        "categoria string, monto decimal(12,2)",
    )

    r = ingresos_quincenales(tx).set_index(["cliente_id", "quincena"])

    assert list(r.columns) == [
        "mes",
        "tipo_transaccion",
        "monto_total",
        "n_movimientos",
    ]
    assert r.loc[("CLI-000001", 1), "monto_total"] == 7500.0
    assert r.loc[("CLI-000001", 2), "monto_total"] == 7500.0
    assert r.loc[("CLI-000002", 1), "monto_total"] == 1000.0
    assert r.loc[("CLI-000002", 2), "monto_total"] == 3000.0
    assert r.loc[("CLI-000002", 2), "n_movimientos"] == 2
    assert set(r["tipo_transaccion"]) == {"nomina", "deposito"}

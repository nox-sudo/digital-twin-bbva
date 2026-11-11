"""
tests/test_ratio_sin_ingreso.py

ratio_endeudamiento de un cliente con deuda y sin ingreso: queda faltante en
Gold y en el feature store (no 0, que es el valor de quien no tiene credito),
y en las etiquetas toma la mayor exposicion observada.
"""

import datetime as dt
import math
import sys
from decimal import Decimal
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gold import kpi_definitions as kpis  # noqa: E402
from src.gold.etiquetas_impago import estandarizar_variables  # noqa: E402
from src.gold.risk_features import _pivotear_kpis_gold  # noqa: E402


@pytest.fixture
def ctx(spark):
    """CON-INGRESO cobra y debe; SIN-INGRESO debe y no cobra; SALDO-CERO
    tiene tarjeta sin saldo y no cobra; SIN-CREDITO no tiene credito."""
    d = Decimal
    tx = spark.createDataFrame(
        [
            ("CON-INGRESO", dt.date(2026, 9, 15), "nomina", d("10000")),
            ("SIN-INGRESO", dt.date(2026, 9, 3), "compra", d("-500")),
            ("SALDO-CERO", dt.date(2026, 9, 3), "compra", d("-500")),
            ("SIN-CREDITO", dt.date(2026, 9, 15), "nomina", d("8000")),
        ],
        "cliente_id string, fecha date, tipo_transaccion string, monto decimal(12,2)",
    )
    clientes = spark.createDataFrame(
        [("CON-INGRESO",), ("SIN-INGRESO",), ("SALDO-CERO",), ("SIN-CREDITO",)],
        "cliente_id string",
    )
    cuentas = spark.createDataFrame(
        [
            ("CON-INGRESO", "prestamo_personal", d("25000")),
            ("SIN-INGRESO", "tarjeta_credito", d("12000")),
            ("SALDO-CERO", "tarjeta_credito", d("0")),
            ("SIN-CREDITO", "cuenta_digital", d("3000")),
        ],
        "cliente_id string, tipo_cuenta string, saldo_actual decimal(12,2)",
    )
    return {"transacciones": tx, "clientes": clientes, "cuentas": cuentas}


def test_deuda_sin_ingreso_queda_faltante(ctx):
    ratio = kpis.kpi_ratio_endeudamiento(ctx).set_index("cliente_id")["valor_numerico"]
    assert ratio["CON-INGRESO"] == pytest.approx(2.5)
    assert math.isnan(ratio["SIN-INGRESO"])
    assert ratio["SALDO-CERO"] == 0.0
    # Sin credito no hay fila: el feature store le pone 0.
    assert "SIN-CREDITO" not in ratio.index


def test_feature_store_distingue_sin_producto_de_faltante(tmp_path):
    ruta = str(tmp_path / "gold.duckdb")
    largo = pd.DataFrame(
        {
            "cliente_id": ["A", "B", "C", "A", "B", "C"],
            "kpi_id": ["ratio_endeudamiento"] * 2 + ["ingreso_mensual_promedio"] * 4,
            "valor_numerico": [2.5, np.nan, 10000.0, 0.0, 8000.0, 9000.0],
            "valor_texto": [None] * 6,
        }
    )
    # La fila de ingreso de A aparece dos veces en el ejemplo; se deja una.
    largo = largo.drop_duplicates(["cliente_id", "kpi_id"])
    con = duckdb.connect(ruta)
    con.execute("CREATE TABLE gold_kpis AS SELECT * FROM largo")
    con.close()

    ancho = _pivotear_kpis_gold(ruta)
    assert ancho.loc["A", "ratio_endeudamiento"] == 2.5
    assert math.isnan(ancho.loc["B", "ratio_endeudamiento"])  # deuda sin ingreso
    assert ancho.loc["C", "ratio_endeudamiento"] == 0.0  # sin credito
    assert (ancho["uso_linea_credito"] == 0.0).all()  # nadie tiene fila: sin tarjeta


def test_etiquetas_dan_la_mayor_exposicion_a_quien_debe_sin_ingreso():
    n = 200
    rng = np.random.default_rng(0)
    features = pd.DataFrame(
        {
            "ratio_endeudamiento": rng.uniform(0, 3, n),
            "uso_linea_credito": rng.uniform(0, 1, n),
            "capacidad_ahorro": rng.normal(2000, 1000, n),
        },
        index=[f"CLI-{i}" for i in range(n)],
    )
    features.iloc[0, 0] = np.nan
    features.iloc[1, 0] = 0.0  # sin credito
    z = estandarizar_variables(features, 0.01, 0.99)
    assert z["ratio_endeudamiento"].iloc[0] == z["ratio_endeudamiento"].max()
    assert z["ratio_endeudamiento"].iloc[1] < z["ratio_endeudamiento"].iloc[0]
    assert not z.isna().any().any()


def test_etiquetas_siguen_rechazando_otros_faltantes():
    features = pd.DataFrame(
        {
            "ratio_endeudamiento": [0.5, 1.0, 2.0],
            "uso_linea_credito": [0.1, 0.2, 0.3],
            "capacidad_ahorro": [100.0, np.nan, 300.0],
        }
    )
    with pytest.raises(ValueError, match="capacidad_ahorro"):
        estandarizar_variables(features, 0.01, 0.99)

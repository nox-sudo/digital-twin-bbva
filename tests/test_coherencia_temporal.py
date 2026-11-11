"""
tests/test_coherencia_temporal.py

Coherencia temporal entre entidades: ningun cliente tiene movimientos antes
de su alta, ningun producto se abre antes que su cliente, ninguna deuda paga
antes de existir. Se prueba en el generador (que no produzca el caso) y en
Silver (que lo detecte si llega de una fuente).
"""

import random
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_synthetic_sources as generador  # noqa: E402
from src.silver.validation import validate_entity  # noqa: E402

FECHA_BASE = date(2026, 9, 30)
MESES_BASE = 12
# Doce meses base mas tres entregas.
MESES = sorted(generador.meses_a_generar(date(2026, 12, 31), MESES_BASE + 3))


@pytest.fixture(scope="module")
def poblacion():
    generador.sembrar(11)
    clientes = generador.generar_clientes(300, FECHA_BASE, meses_historial=MESES_BASE)
    cuentas = generador.generar_cuentas(clientes, FECHA_BASE)
    perfiles = generador.generar_perfiles(clientes, cuentas, 11)
    return clientes, cuentas, perfiles


def _mes(cuentas, perfiles, anio, mes):
    semilla = generador.semilla_mes(11, anio, mes)
    random.seed(semilla)
    np.random.seed(semilla)
    generador.Faker.seed(semilla)
    return generador.generar_transacciones_mes(cuentas, anio, mes, perfiles)


def test_el_alta_es_anterior_al_historial(poblacion):
    clientes, _, _ = poblacion
    inicio = generador.inicio_historial(FECHA_BASE, MESES_BASE)
    assert inicio == date(2025, 10, 1)
    altas = pd.to_datetime(clientes["fecha_alta"]).dt.date
    assert (altas < inicio).all()
    # El rango sigue siendo amplio: hay altas de hace mas de dos anios.
    assert (altas < date(2024, 9, 30)).any()


def test_ningun_producto_se_abre_antes_que_su_cliente(poblacion):
    clientes, cuentas, _ = poblacion
    alta = dict(zip(clientes["cliente_id"], clientes["fecha_alta"]))
    ultima = date(2026, 8, 30)
    for c in cuentas:
        assert alta[c["cliente_id"]] <= c["fecha_apertura"], c["cuenta_id"]
        assert date.fromisoformat(c["fecha_apertura"]) <= ultima, c["cuenta_id"]


def test_ningun_movimiento_antes_del_alta(poblacion):
    clientes, cuentas, perfiles = poblacion
    alta = dict(zip(clientes["cliente_id"], clientes["fecha_alta"]))
    total = 0
    for anio, mes in MESES:
        tx = _mes(cuentas, perfiles, anio, mes)
        fechas = tx["fecha"].astype(str)
        altas = tx["cliente_id"].map(alta)
        assert (fechas >= altas).all(), (anio, mes)
        total += len(tx)
    assert total > 0


def test_la_tarjeta_no_paga_antes_de_abrirse(poblacion):
    _, cuentas, perfiles = poblacion
    # A lo mas una tarjeta por cliente: el pago se identifica por cliente.
    tarjeta_de = {
        c["cliente_id"]: c for c in cuentas if c["tipo_cuenta"] == "tarjeta_credito"
    }
    recientes = [t for t in tarjeta_de.values() if t["fecha_apertura"] >= "2025-11-01"]
    assert recientes, "la poblacion debe traer tarjetas abiertas dentro del historial"
    pagos = 0
    for anio, mes in MESES:
        tx = _mes(cuentas, perfiles, anio, mes)
        de_tarjeta = tx[tx["contraparte"] == "Pago Tarjeta de Crédito"]
        for cliente_id in de_tarjeta["cliente_id"]:
            t = tarjeta_de[cliente_id]
            assert generador._meses_desde_apertura(t["fecha_apertura"], anio, mes) >= 1
            pagos += 1
    assert pagos > 0


def test_historial_largo_no_deja_el_rango_de_alta_vacio():
    generador.sembrar(3)
    clientes = generador.generar_clientes(20, FECHA_BASE, meses_historial=48)
    inicio = generador.inicio_historial(FECHA_BASE, 48)
    assert (pd.to_datetime(clientes["fecha_alta"]).dt.date < inicio).all()


def test_rango_de_fechas_invertido_falla():
    with pytest.raises(ValueError, match="rango de fechas vacio"):
        generador.fecha_entre_fechas(date(2026, 1, 2), date(2026, 1, 1))


REGLAS_TX = {
    "date_not_before": {
        "fecha": {
            "via": "cuenta_id",
            "references_entity": "cuentas",
            "references_column": "fecha_apertura",
        }
    }
}


def test_silver_aisla_movimiento_anterior_a_su_cuenta(spark):
    cuentas = spark.createDataFrame(
        [("CTA-1", "2025-06-15"), ("CTA-2", "2026-03-01")],
        ["cuenta_id", "fecha_apertura"],
    )
    tx = spark.createDataFrame(
        [
            ("TX-1", "CTA-1", "2026-01-10"),
            ("TX-2", "CTA-2", "2026-02-27"),  # antes de abrir la cuenta
            ("TX-3", "CTA-2", "2026-03-01"),  # el mismo dia si vale
        ],
        ["transaccion_id", "cuenta_id", "fecha"],
    )
    valido, cuarentena, reporte = validate_entity(
        tx, "transacciones", REGLAS_TX, silver_context={"cuentas": cuentas}
    )
    assert reporte.violaciones_por_regla == {"datebefore_fecha": 1}
    assert [r.transaccion_id for r in cuarentena.collect()] == ["TX-2"]
    assert sorted(r.transaccion_id for r in valido.collect()) == ["TX-1", "TX-3"]
    assert valido.columns == ["transaccion_id", "cuenta_id", "fecha"]
    motivo = cuarentena.collect()[0]
    assert motivo._violaciones[0].regla == "datebefore"
    assert motivo._violaciones[0].columna == "fecha"


def test_silver_sin_referencia_no_cuenta_como_fecha_anterior(spark):
    """Una cuenta inexistente es asunto de la llave foranea, no de esta regla."""
    cuentas = spark.createDataFrame(
        [("CTA-1", "2025-06-15")], ["cuenta_id", "fecha_apertura"]
    )
    tx = spark.createDataFrame(
        [("TX-1", "CTA-9", "2020-01-01")], ["transaccion_id", "cuenta_id", "fecha"]
    )
    reglas = dict(
        REGLAS_TX,
        foreign_keys={
            "cuenta_id": {
                "references_entity": "cuentas",
                "references_column": "cuenta_id",
            }
        },
    )
    _, _, reporte = validate_entity(
        tx, "transacciones", reglas, silver_context={"cuentas": cuentas}
    )
    assert reporte.violaciones_por_regla == {"fk_cuenta_id": 1}


def test_la_regla_esta_declarada_para_cuentas_y_transacciones():
    import yaml

    reglas = yaml.safe_load(
        Path("config/business_rules.yaml").read_text(encoding="utf-8")
    )
    assert (
        reglas["cuentas"]["date_not_before"]["fecha_apertura"]["references_column"]
        == "fecha_alta"
    )
    assert (
        reglas["transacciones"]["date_not_before"]["fecha"]["references_column"]
        == "fecha_apertura"
    )

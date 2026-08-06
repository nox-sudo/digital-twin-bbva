"""
tests/test_validation.py

Pruebas automatizadas del motor generico de validacion. Formaliza los
4 escenarios que se probaron manualmente durante el desarrollo de
Silver: not_null, numeric_ranges, unique, y foreign_keys.

Cada test inyecta datos corruptos deliberadamente y confirma que el
motor los aisla en cuarentena con el motivo correcto, sin afectar las
filas validas.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.silver.validation import validate_entity  # noqa: E402

CLIENTES_RULES = {
    "primary_key": "cliente_id",
    "not_null": ["cliente_id", "ingreso_mensual_declarado"],
    "unique": ["cliente_id"],
    "numeric_ranges": {"ingreso_mensual_declarado": {"min": 0.01}},
}

CUENTAS_RULES = {
    "primary_key": "cuenta_id",
    "not_null": ["cuenta_id", "cliente_id"],
    "foreign_keys": {
        "cliente_id": {
            "references_entity": "clientes",
            "references_column": "cliente_id",
        }
    },
}


def test_datos_limpios_no_generan_cuarentena(spark):
    """Caso base: datos sin violaciones deben pasar completos a Silver."""
    df = spark.createDataFrame(
        [("CLI-000001", 15000.0), ("CLI-000002", 22000.0)],
        ["cliente_id", "ingreso_mensual_declarado"],
    )
    valido, cuarentena, reporte = validate_entity(df, "clientes", CLIENTES_RULES)

    assert reporte.filas_entrada == 2
    assert reporte.filas_validas == 2
    assert reporte.filas_cuarentena == 0
    assert valido.count() == 2
    assert cuarentena.count() == 0


def test_not_null_aisla_valor_nulo(spark):
    """Un ingreso nulo debe caer en cuarentena por la regla not_null."""
    df = spark.createDataFrame(
        [("CLI-000001", 15000.0), ("CLI-000002", None)],
        ["cliente_id", "ingreso_mensual_declarado"],
    )
    valido, cuarentena, reporte = validate_entity(df, "clientes", CLIENTES_RULES)

    assert reporte.filas_validas == 1
    assert reporte.filas_cuarentena == 1
    assert "not_null_ingreso_mensual_declarado" in reporte.violaciones_por_regla
    assert valido.count() == 1


def test_numeric_range_aisla_ingreso_negativo(spark):
    """Un ingreso negativo debe caer en cuarentena por numeric_ranges."""
    df = spark.createDataFrame(
        [("CLI-000001", 15000.0), ("CLI-000002", -500.0)],
        ["cliente_id", "ingreso_mensual_declarado"],
    )
    valido, cuarentena, reporte = validate_entity(df, "clientes", CLIENTES_RULES)

    assert reporte.filas_validas == 1
    assert reporte.filas_cuarentena == 1
    assert "range_ingreso_mensual_declarado" in reporte.violaciones_por_regla


def test_unique_aisla_duplicados(spark):
    """cliente_id duplicado debe caer en cuarentena por la regla unique."""
    df = spark.createDataFrame(
        [("CLI-000001", 15000.0), ("CLI-000001", 16000.0), ("CLI-000002", 22000.0)],
        ["cliente_id", "ingreso_mensual_declarado"],
    )
    valido, cuarentena, reporte = validate_entity(df, "clientes", CLIENTES_RULES)

    assert reporte.filas_validas == 1
    assert reporte.filas_cuarentena == 2
    assert "unique_cliente_id" in reporte.violaciones_por_regla


def test_foreign_key_aisla_referencia_inexistente(spark):
    """Una cuenta con cliente_id inexistente debe caer en cuarentena."""
    clientes_df = spark.createDataFrame([("CLI-000001",)], ["cliente_id"])
    cuentas_df = spark.createDataFrame(
        [("CTA-000001", "CLI-000001"), ("CTA-000002", "CLI-NOEXISTE")],
        ["cuenta_id", "cliente_id"],
    )

    valido, cuarentena, reporte = validate_entity(
        cuentas_df,
        "cuentas",
        CUENTAS_RULES,
        silver_context={"clientes": clientes_df},
    )

    assert reporte.filas_validas == 1
    assert reporte.filas_cuarentena == 1
    assert "fk_cliente_id" in reporte.violaciones_por_regla

"""
tests/test_calidad.py

Pruebas del registro historico de calidad (src/calidad/registro.py):
errores de tipo que antes se volvian NULL en silencio, perfil de nulos,
desglose de violaciones e incidencias sin valores (sin PII).
"""

import sys
from pathlib import Path

import pytest
from pyspark.sql import functions as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.calidad.registro import (  # noqa: E402
    descomponer_violacion,
    incidencias,
    perfil_columnas,
    tipar_con_control,
)
from src.silver.typing_rules import type_transacciones  # noqa: E402
from src.silver.validation import validate_entity  # noqa: E402

REGLAS_TX = {
    "not_null": ["transaccion_id", "fecha", "monto"],
    "unique": ["transaccion_id"],
}


@pytest.fixture
def transacciones_crudas(spark):
    """Como llegan a Bronze: todo texto, con metadata de trazabilidad."""
    filas = [
        ("TX-1", "2026-09-01", "150.00", "s42_base", "t.csv"),
        ("TX-2", "2026-09-02", "12,3x", "s42_base", "t.csv"),  # monto corrupto
        ("TX-3", "2026-02-30", "80.00", "s42_base", "t.csv"),  # fecha imposible
        ("TX-4", "2026-09-04", None, "s42_base", "t.csv"),  # monto ausente
    ]
    return spark.createDataFrame(
        filas, ["transaccion_id", "fecha", "monto", "_sesion_id", "_source_file"]
    )


@pytest.mark.parametrize(
    "columna, esperado",
    [
        ("_viol_not_null_curp", ("not_null", "curp")),
        ("_viol_condnull_rfc_0", ("condnull", "rfc")),
        ("_viol_fk_cuenta_id", ("fk", "cuenta_id")),
        ("_viol_tipo_monto", ("tipo", "monto")),
        (
            "_viol_range_ingreso_mensual_declarado",
            ("range", "ingreso_mensual_declarado"),
        ),
    ],
)
def test_descomponer_violacion(columna, esperado):
    assert descomponer_violacion(columna) == esperado


def test_tipo_invalido_se_distingue_de_nulo(transacciones_crudas):
    tipado = tipar_con_control(transacciones_crudas, type_transacciones)
    _, cuarentena, reporte = validate_entity(tipado, "transacciones", REGLAS_TX)

    motivos = {
        f.transaccion_id: {(v.regla, v.columna) for v in f._violaciones}
        for f in cuarentena.collect()
    }
    # Venia un valor que no se pudo convertir: tipo (y por eso tambien nulo).
    assert ("tipo", "monto") in motivos["TX-2"]
    assert ("tipo", "fecha") in motivos["TX-3"]
    # No venia nada: solo not_null, sin acusar un error de tipo.
    assert motivos["TX-4"] == {("not_null", "monto")}
    assert "TX-1" not in motivos
    assert reporte.filas_cuarentena == 3


def test_incidencias_una_fila_por_regla_y_sin_valores(transacciones_crudas):
    tipado = tipar_con_control(transacciones_crudas, type_transacciones)
    _, cuarentena, _ = validate_entity(tipado, "transacciones", REGLAS_TX)

    registro = incidencias(cuarentena, "transacciones", "transaccion_id", "silver_test")
    filas = registro.collect()

    # TX-2 y TX-3: tipo + not_null cada una; TX-4: not_null.
    assert len(filas) == 5
    assert {f.llave for f in filas} == {"TX-2", "TX-3", "TX-4"}
    assert all(f.sesion_id == "s42_base" and f.archivo_origen == "t.csv" for f in filas)
    # Solo llave, regla y columna: ningun valor de los datos.
    assert set(registro.columns) == {
        "run_id",
        "fecha_corrida",
        "capa",
        "entidad",
        "llave",
        "regla",
        "columna",
        "sesion_id",
        "archivo_origen",
    }


def test_cuarentena_vacia_produce_registro_vacio_con_esquema(spark):
    limpio = spark.createDataFrame(
        [("TX-1", "2026-09-01", "1.00")], ["transaccion_id", "fecha", "monto"]
    )
    _, cuarentena, _ = validate_entity(
        type_transacciones(limpio), "transacciones", REGLAS_TX
    )
    registro = incidencias(cuarentena, "transacciones", "transaccion_id", "silver_test")
    assert registro.count() == 0
    assert "regla" in registro.columns


def test_perfil_cuenta_nulos_incluso_en_columnas_opcionales(spark):
    df = spark.createDataFrame(
        [("CLI-1", "Pérez", None), ("CLI-2", None, None), ("CLI-3", "Ruiz", "x")],
        ["cliente_id", "apellido_materno", "colonia"],
    ).withColumn(
        "_sesion_id", F.lit("s")
    )  # metadata: no entra al perfil

    perfil = {
        f.columna: (f.filas, f.nulos)
        for f in perfil_columnas(df, "clientes", "silver_test").collect()
    }
    assert perfil == {
        "cliente_id": (3, 0),
        "apellido_materno": (3, 1),
        "colonia": (3, 2),
    }


def test_valor_corrupto_no_detiene_validacion(spark):
    """Con el modo ANSI de Spark 4, comparar "abc" contra numeros lanzaba
    un error que detenia todo Silver. Ahora va a cuarentena."""
    df = spark.createDataFrame(
        [("C1", "28"), ("C2", "abc"), ("C3", None)], ["cuenta_id", "plazo_dias"]
    )
    valido, cuarentena, reporte = validate_entity(
        df, "cetes_inversiones", {"allowed_values": {"plazo_dias": [28, 91, 182, 364]}}
    )
    # Ninguna fila desaparece: lo que no es valido esta en cuarentena.
    assert valido.count() + cuarentena.count() == 3
    assert {f.cuenta_id for f in cuarentena.collect()} == {"C2"}

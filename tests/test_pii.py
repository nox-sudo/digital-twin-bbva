"""
tests/test_pii.py

Pruebas de identificadores (CURP, RFC) y de la proteccion de PII en
Silver:

- Los algoritmos de Python reproducen los ejemplos publicos de RENAPO y
  el SAT.
- La validacion nativa de Spark (src/silver/identificadores_spark.py)
  coincide con la de Python sobre identificadores validos y alterados.
- El HMAC calculado en Spark es identico al de hmac.new() de Python.
- Las mascaras y la politica no dejan ningun campo sensible en claro.
"""

import hashlib
import hmac
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from pyspark.sql import functions as F
from pyspark.sql.types import DateType, StringType, StructField, StructType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.identificadores import (  # noqa: E402
    construir_curp,
    construir_rfc,
    validar_curp,
    validar_rfc,
)
from src.silver.identificadores_spark import curp_valida, rfc_valido  # noqa: E402
from src.silver.pii import columnas_en_claro, hmac_sha256, proteger_pii  # noqa: E402

# --- Algoritmos de referencia (Python) --------------------------------------


def test_curp_ejemplo_publico_renapo():
    curp = construir_curp(
        "Gloria", "Hernández", "García", date(1956, 4, 27), "M", "VZ", "0"
    )
    assert curp == "HEGG560427MVZRRL04"


def test_rfc_ejemplo_publico_sat():
    assert construir_rfc("Juan", "Barrios", "Fernández", date(1970, 12, 13)) == (
        "BAFJ701213SBA"
    )


def test_curp_particulas_enie_y_nombre_compuesto():
    # "de la" se omite, la Ñ se vuelve X, y "María" cede al segundo nombre.
    curp = construir_curp(
        "María Elena", "de la Garza", "Ñúñez", date(2003, 1, 5), "M", "SL", "A"
    )
    assert curp.startswith("GAXE030105MSL")
    assert validar_curp(curp, date(2003, 1, 5))


def test_curp_corrige_palabra_inconveniente():
    # Camacho Castro, Alberto -> CACA, que RENAPO corrige a CXCA.
    curp = construir_curp(
        "Alberto", "Camacho", "Castro", date(1990, 1, 1), "H", "DF", "3"
    )
    assert curp.startswith("CXCA")


def test_validar_detecta_fecha_ajena_y_digito_alterado():
    curp = "HEGG560427MVZRRL04"
    assert validar_curp(curp, date(1956, 4, 27))
    assert not validar_curp(curp, date(1956, 4, 28))
    assert not validar_curp(curp[:-1] + "5")
    assert not validar_curp("HEGG560431MVZRRL04")  # 31 de abril no existe


# --- Spark == Python ---------------------------------------------------------


def _casos_identificadores(n=150, semilla=7):
    """CURP y RFC validos, mas versiones alteradas de distintas formas."""
    rng = random.Random(semilla)
    nombres = ["Ana", "José Luis", "María", "Ñoño", "Pedro", "Elena", "Iván"]
    apellidos = ["López", "de la O", "Ibáñez", "Xu", "Ruiz", "Ángeles", "Moya"]
    casos = []
    for _ in range(n):
        nacimiento = date(1950, 1, 1) + timedelta(days=rng.randint(0, 20000))
        nombre, pat, mat = (
            rng.choice(nombres),
            rng.choice(apellidos),
            rng.choice(apellidos),
        )
        homoclave = (
            rng.choice("ABCDE") if nacimiento.year >= 2000 else rng.choice("0123")
        )
        curp = construir_curp(
            nombre,
            pat,
            mat,
            nacimiento,
            rng.choice("HM"),
            rng.choice(["SL", "DF", "NE"]),
            homoclave,
        )
        rfc = construir_rfc(nombre, pat, mat, nacimiento)
        alteracion = rng.choice(["ninguna", "digito", "fecha", "caracter", "corta"])
        if alteracion == "digito":
            curp = curp[:-1] + str((int(curp[-1]) + 1) % 10)
            rfc = rfc[:-1] + ("0" if rfc[-1] != "0" else "1")
        elif alteracion == "fecha":
            nacimiento = nacimiento + timedelta(days=1)
        elif alteracion == "caracter":
            curp = curp[:5] + ("9" if curp[5] != "9" else "8") + curp[6:]
            rfc = rfc[:5] + ("9" if rfc[5] != "9" else "8") + rfc[6:]
        elif alteracion == "corta":
            curp, rfc = curp[:-1], rfc[:-1]
        casos.append((curp, rfc, nacimiento))
    return casos


def test_validacion_spark_coincide_con_python(spark):
    casos = _casos_identificadores()
    esquema = StructType(
        [
            StructField("curp", StringType()),
            StructField("rfc", StringType()),
            StructField("fecha_nacimiento", DateType()),
        ]
    )
    df = spark.createDataFrame(casos, esquema)
    resultado = df.select(
        "curp",
        "rfc",
        "fecha_nacimiento",
        F.coalesce(curp_valida("curp", "fecha_nacimiento"), F.lit(False)).alias("c_ok"),
        F.coalesce(rfc_valido("rfc", "fecha_nacimiento"), F.lit(False)).alias("r_ok"),
    ).collect()

    for fila in resultado:
        assert fila.c_ok == validar_curp(fila.curp, fila.fecha_nacimiento), fila.curp
        assert fila.r_ok == validar_rfc(fila.rfc, fila.fecha_nacimiento), fila.rfc
    # La prueba tiene sentido solo si hay de ambos tipos.
    assert {f.c_ok for f in resultado} == {True, False}


# --- HMAC y mascaras ----------------------------------------------------------


@pytest.mark.parametrize("sal", ["corta", "x" * 64, "y" * 100])
def test_hmac_spark_identico_a_python(spark, sal):
    valores = ["HEGG560427MVZRRL04", "Ñúñez", "", "6670542351"]
    df = spark.createDataFrame([(v,) for v in valores] + [(None,)], ["valor"])
    filas = df.select("valor", hmac_sha256(F.col("valor"), sal).alias("h")).collect()

    for fila in filas:
        if fila.valor is None:
            assert fila.h is None
        else:
            esperado = hmac.new(
                sal.encode(), fila.valor.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            assert fila.h == esperado


POLITICA = {
    "hash_y_mascara": {
        "curp": "identificador",
        "telefono": "telefono",
        "email": "email",
    },
    "solo_hash": ["nombre"],
    "descartar": ["calle"],
}


@pytest.fixture
def clientes_df(spark):
    return spark.createDataFrame(
        [
            (
                "CLI-1",
                "Gloria",
                "HEGG560427MVZRRL04",
                "6670542351",
                "g.h7@example.com",
                "Uno",
            ),
            ("CLI-2", "Gloria", "HEGG560427MVZRRL04", "5501234567", None, "Dos"),
        ],
        ["cliente_id", "nombre", "curp", "telefono", "email", "calle"],
    )


def test_politica_no_deja_campos_sensibles_en_claro(clientes_df):
    protegido = proteger_pii(clientes_df, POLITICA, "sal-de-prueba")

    assert columnas_en_claro(protegido, POLITICA) == []
    assert set(protegido.columns) == {
        "cliente_id",
        "nombre_hash",
        "curp_hash",
        "curp_mascara",
        "telefono_hash",
        "telefono_mascara",
        "email_hash",
        "email_mascara",
    }


def test_mascaras_y_hash_deterministico(clientes_df):
    filas = (
        proteger_pii(clientes_df, POLITICA, "sal-de-prueba")
        .orderBy("cliente_id")
        .collect()
    )

    assert filas[0].curp_mascara == "HEGG************04"
    assert filas[0].telefono_mascara == "******2351"
    assert filas[0].email_mascara == "g***@example.com"
    assert filas[1].email_hash is None and filas[1].email_mascara is None
    # Mismo valor, mismo hash: permite unir y deduplicar sin ver el dato.
    assert filas[0].curp_hash == filas[1].curp_hash
    assert filas[0].nombre_hash == filas[1].nombre_hash


def test_sal_distinta_hash_distinto(clientes_df):
    a = proteger_pii(clientes_df, POLITICA, "sal-a").first().curp_hash
    b = proteger_pii(clientes_df, POLITICA, "sal-b").first().curp_hash
    assert a != b


# --- Integracion: generador -> tipado -> reglas reales -> politica real -----


def test_clientes_generados_pasan_reglas_y_salen_protegidos(spark, tmp_path):
    import yaml

    import generate_synthetic_sources as generador
    from src.silver.typing_rules import type_clientes
    from src.silver.validation import validate_entity

    generador.main(
        [
            "--clientes", "60", "--meses", "1", "--fecha-referencia", "2026-09-30",
            "--config", str(tmp_path / "no-existe.yaml"),
            "--sesiones-dir", str(tmp_path / "sesiones"),
            "--out", str(tmp_path / "activa"),
        ]
    )  # fmt: skip
    raiz = Path(__file__).resolve().parents[1]
    reglas = yaml.safe_load(open(raiz / "config/business_rules.yaml"))["clientes"]
    politica = yaml.safe_load(open(raiz / "config/politica_pii.yaml"))["clientes"]

    # Como Bronze: todo como texto, sin inferir tipos.
    crudo = spark.read.csv(str(tmp_path / "activa/clientes.csv"), header=True)
    clientes = type_clientes(crudo)

    valido, cuarentena, reporte = validate_entity(clientes, "clientes", reglas)
    assert reporte.filas_cuarentena == 0, reporte.violaciones_por_regla

    # Tres alteraciones tipicas de captura: digito de CURP cambiado,
    # telefono incompleto, RFC faltante para quien trabaja.
    alterado = (
        clientes.withColumn(
            "curp",
            F.when(
                F.col("cliente_id") == "CLI-000001",
                F.concat(F.substring("curp", 1, 17), F.lit("X")),
            ).otherwise(F.col("curp")),
        )
        .withColumn(
            "telefono",
            F.when(F.col("cliente_id") == "CLI-000002", F.lit("66712")).otherwise(
                F.col("telefono")
            ),
        )
        .withColumn(
            "ocupacion",
            F.when(F.col("cliente_id") == "CLI-000003", F.lit("Empleado")).otherwise(
                F.col("ocupacion")
            ),
        )
        .withColumn(
            "rfc",
            F.when(F.col("cliente_id") == "CLI-000003", F.lit(None)).otherwise(
                F.col("rfc")
            ),
        )
    )
    _, cuarentena, reporte = validate_entity(alterado, "clientes", reglas)
    motivos = {
        f.cliente_id: f._motivo_cuarentena
        for f in cuarentena.select("cliente_id", "_motivo_cuarentena").collect()
    }
    assert "ident_curp" in motivos["CLI-000001"]
    assert "pattern_telefono" in motivos["CLI-000002"]
    assert "condnull_rfc" in motivos["CLI-000003"]

    for salida in (valido, cuarentena):
        protegida = proteger_pii(salida, politica, "sal-de-prueba")
        assert columnas_en_claro(protegida, politica) == []
        assert "curp_hash" in protegida.columns and "calle" not in protegida.columns

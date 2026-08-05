"""
src/silver/typing_rules.py

Unica parte de Silver que SI es especifica por entidad: convertir tipos
(texto -> fecha, texto -> decimal). El motor de validacion en
validation.py es completamente generico; esto no podria serlo, porque
solo el creador del esquema sabe que "fecha_alta" es una fecha y no un
texto cualquiera.

Se mantiene deliberadamente pequeno: una funcion por entidad, sin logica
de negocio, sin validaciones (eso ya vive en validation.py).
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType


def type_clientes(df: DataFrame) -> DataFrame:
    return (
        df
        .withColumn("fecha_nacimiento", F.to_date("fecha_nacimiento"))
        .withColumn("fecha_alta", F.to_date("fecha_alta"))
        .withColumn("ingreso_mensual_declarado", F.col("ingreso_mensual_declarado").cast(DecimalType(12, 2)))
    )


def type_cuentas(df: DataFrame) -> DataFrame:
    return (
        df
        .withColumn("fecha_apertura", F.to_date("fecha_apertura"))
        .withColumn("saldo_actual", F.col("saldo_actual").cast(DecimalType(12, 2)))
        .withColumn("limite_credito", F.col("limite_credito").cast(DecimalType(12, 2)))
        .withColumn("monto_original", F.col("monto_original").cast(DecimalType(12, 2)))
    )


def type_cetes_inversiones(df: DataFrame) -> DataFrame:
    return (
        df
        .withColumn("fecha_inicio", F.to_date("fecha_inicio"))
        .withColumn("fecha_vencimiento", F.to_date("fecha_vencimiento"))
        .withColumn("monto_invertido", F.col("monto_invertido").cast(DecimalType(12, 2)))
        .withColumn("tasa_interes_anual", F.col("tasa_interes_anual").cast(DecimalType(6, 4)))
    )


def type_transacciones(df: DataFrame) -> DataFrame:
    return (
        df
        .withColumn("fecha", F.to_date("fecha"))
        .withColumn("monto", F.col("monto").cast(DecimalType(12, 2)))
    )


# Registro central: entidad -> funcion de tipado.
# Agregar una entidad nueva significa agregar una linea aqui, no
# modificar el orquestador.
TYPING_REGISTRY = {
    "clientes": type_clientes,
    "cuentas": type_cuentas,
    "cetes_inversiones": type_cetes_inversiones,
    "transacciones": type_transacciones,
}

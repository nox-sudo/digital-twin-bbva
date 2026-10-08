"""
src/silver/typing_rules.py

Unica parte de Silver que SI es especifica por entidad: convertir tipos
(texto -> fecha, texto -> decimal). El motor de validacion en
validation.py es completamente generico; esto no podria serlo, porque
solo el creador del esquema sabe que "fecha_alta" es una fecha y no un
texto cualquiera.

Se mantiene deliberadamente pequeno: una funcion por entidad, sin logica
de negocio, sin validaciones (eso ya vive en validation.py).

Todas las conversiones usan try_cast. Spark 4 activa por default el
modo ANSI (spark.sql.ansi.enabled), en el que un cast invalido LANZA un
error en vez de devolver NULL: una sola fecha imposible ("2026-02-30") o
un monto corrupto ("12,3x") en cualquier archivo detenia todo Silver, en
lugar de mandar esa fila a cuarentena. try_cast devuelve NULL y
src/calidad/registro.py (tipar_con_control) marca ese NULL como una
violacion "tipo", distinta de un valor que simplemente no venia.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType, IntegerType


def type_clientes(df: DataFrame) -> DataFrame:
    return (
        df.withColumn("fecha_nacimiento", F.col("fecha_nacimiento").try_cast("date"))
        .withColumn("fecha_alta", F.col("fecha_alta").try_cast("date"))
        .withColumn(
            "ingreso_mensual_declarado",
            F.col("ingreso_mensual_declarado").try_cast(DecimalType(12, 2)),
        )
    )


def type_cuentas(df: DataFrame) -> DataFrame:
    return (
        df.withColumn("fecha_apertura", F.col("fecha_apertura").try_cast("date"))
        .withColumn("saldo_actual", F.col("saldo_actual").try_cast(DecimalType(12, 2)))
        .withColumn(
            "limite_credito", F.col("limite_credito").try_cast(DecimalType(12, 2))
        )
        .withColumn(
            "monto_original", F.col("monto_original").try_cast(DecimalType(12, 2))
        )
        # plazo_meses (cuentas): escalar, el plazo de esta cuenta
        # especifica en meses. No confundir con plazos_meses (lista) de
        # catalogo_productos, que son los plazos que el producto ofrece
        # en general. Sin este cast, fluia sin tipo explicito desde
        # Bronze - no tenia uso downstream, pero quedaba inconsistente.
        .withColumn("plazo_meses", F.col("plazo_meses").try_cast(IntegerType()))
    )


def type_cetes_inversiones(df: DataFrame) -> DataFrame:
    return (
        df.withColumn("fecha_inicio", F.col("fecha_inicio").try_cast("date"))
        .withColumn("fecha_vencimiento", F.col("fecha_vencimiento").try_cast("date"))
        .withColumn(
            "monto_invertido", F.col("monto_invertido").try_cast(DecimalType(12, 2))
        )
        .withColumn(
            "tasa_interes_anual",
            F.col("tasa_interes_anual").try_cast(DecimalType(6, 4)),
        )
    )


def type_transacciones(df: DataFrame) -> DataFrame:
    return df.withColumn("fecha", F.col("fecha").try_cast("date")).withColumn(
        "monto", F.col("monto").try_cast(DecimalType(12, 2))
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

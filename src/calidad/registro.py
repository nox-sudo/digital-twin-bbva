"""
src/calidad/registro.py

Registro historico de calidad de datos. Complementa a la cuarentena:

- La cuarentena (data/silver_quarantine/) es la foto de la ultima
  corrida: que filas estan rechazadas AHORA. Se sobrescribe.
- El registro (data/calidad/) acumula TODAS las corridas, para poder
  responder "cuantas CURP invalidas llegaron en la entrega de octubre" o
  "desde cuando viene vacio este campo".

Tres piezas:

1. tipar_con_control(): marca los valores que llegaron pero no se
   pudieron convertir a su tipo ("12,3x" en un monto, "2026-02-30" en
   una fecha). Sin esto, la conversion los volvia NULL en silencio y la
   cuarentena reportaba "es nulo" en vez de "venia corrupto".
2. perfil_columnas(): nulos por columna en lo que llego, incluidas las
   columnas opcionales que ninguna regla obliga.
3. incidencias(): una fila por cada regla violada por cada fila
   rechazada.

Privacidad: el registro guarda la llave de la fila (cliente_id,
cuenta_id, transaccion_id), la regla y la columna, NUNCA el valor. Si
guardara valores, el historial de calidad seria una copia de la PII que
Silver protege. Para ver el valor real se sigue la llave hasta Bronze,
la zona autorizada a tenerlo.
"""

from datetime import datetime

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import LongType, StringType, StructField, StructType

# Prefijos de las columnas _viol_<regla>_<columna> que genera el motor de
# validacion (src/silver/validation.py), mas "tipo" (este modulo). El
# orden importa: not_null antes que cualquier prefijo que pudiera
# coincidir con su inicio.
REGLAS = [
    "not_null",
    "unique",
    "allowed",
    "range",
    "notzero",
    "futuredate",
    "condnull",
    "pattern",
    "ident",
    "fk",
    "tipo",
]

ESQUEMA_INCIDENCIAS = StructType(
    [
        StructField("run_id", StringType()),
        StructField("fecha_corrida", StringType()),
        StructField("capa", StringType()),
        StructField("entidad", StringType()),
        StructField("llave", StringType()),
        StructField("regla", StringType()),
        StructField("columna", StringType()),
        StructField("sesion_id", StringType()),
        StructField("archivo_origen", StringType()),
    ]
)

ESQUEMA_PERFIL = StructType(
    [
        StructField("run_id", StringType()),
        StructField("fecha_corrida", StringType()),
        StructField("entidad", StringType()),
        StructField("columna", StringType()),
        StructField("filas", LongType()),
        StructField("nulos", LongType()),
    ]
)


def nuevo_run_id(capa: str) -> str:
    return f"{capa}_{datetime.now():%Y%m%d_%H%M%S}"


def descomponer_violacion(nombre_columna: str) -> tuple[str, str]:
    """_viol_condnull_rfc_0 -> ("condnull", "rfc")
    _viol_not_null_curp   -> ("not_null", "curp")"""
    resto = nombre_columna.removeprefix("_viol_")
    for regla in REGLAS:
        if resto.startswith(regla + "_"):
            columna = resto[len(regla) + 1 :]  # noqa: E203
            if regla == "condnull":
                # condnull lleva un indice al final (varias reglas por columna).
                columna = columna.rsplit("_", 1)[0]
            return regla, columna
    return "desconocida", resto


def tipar_con_control(df: DataFrame, typing_fn) -> DataFrame:
    """Aplica el tipado de la entidad y agrega _viol_tipo_<col> para cada
    columna cuyo tipo cambio y cuyo valor original no estaba vacio pero
    no se pudo convertir. El motor de validacion toma estas columnas
    como cualquier otra violacion: la fila va a cuarentena con motivo
    tipo_<col>."""
    columnas = [c for c in df.columns if not c.startswith("_")]
    tipos_antes = dict(df.dtypes)
    respaldo = {c: f"_orig_{c}" for c in columnas}
    for c, r in respaldo.items():
        df = df.withColumn(r, F.col(c))

    tipado = typing_fn(df)
    tipos_despues = dict(tipado.dtypes)
    for c, r in respaldo.items():
        if tipos_despues.get(c) != tipos_antes[c]:
            original = F.col(r)
            tipado = tipado.withColumn(
                f"_viol_tipo_{c}",
                original.isNotNull()
                & (F.trim(original.cast("string")) != "")
                & F.col(c).isNull(),
            )
    return tipado.drop(*respaldo.values())


def perfil_columnas(df: DataFrame, entidad: str, run_id: str) -> DataFrame:
    """Filas y nulos por columna de negocio (no las de metadata _*), en
    una sola pasada sobre los datos."""
    spark = df.sparkSession
    columnas = [c for c in df.columns if not c.startswith("_")]
    if not columnas:
        return spark.createDataFrame([], ESQUEMA_PERFIL)
    conteos = df.agg(
        F.count(F.lit(1)).alias("__filas"),
        *[F.sum(F.col(c).isNull().cast("long")).alias(c) for c in columnas],
    ).first()
    fecha = datetime.now().isoformat(timespec="seconds")
    filas = [
        (run_id, fecha, entidad, c, int(conteos["__filas"]), int(conteos[c] or 0))
        for c in columnas
    ]
    return spark.createDataFrame(filas, ESQUEMA_PERFIL)


def incidencias(
    df_cuarentena: DataFrame,
    entidad: str,
    llave: str,
    run_id: str,
    capa: str = "silver",
) -> DataFrame:
    """Una fila por cada (fila rechazada, regla violada). Toma la columna
    _violaciones que deja validate_entity en la cuarentena."""
    fecha = datetime.now().isoformat(timespec="seconds")
    return df_cuarentena.select(
        F.col(llave).cast("string").alias("llave"),
        (
            F.col("_sesion_id").cast("string").alias("sesion_id")
            if "_sesion_id" in df_cuarentena.columns
            else F.lit(None).cast("string").alias("sesion_id")
        ),
        (
            F.col("_source_file").cast("string").alias("archivo_origen")
            if "_source_file" in df_cuarentena.columns
            else F.lit(None).cast("string").alias("archivo_origen")
        ),
        F.explode("_violaciones").alias("v"),
    ).select(
        F.lit(run_id).alias("run_id"),
        F.lit(fecha).alias("fecha_corrida"),
        F.lit(capa).alias("capa"),
        F.lit(entidad).alias("entidad"),
        "llave",
        F.col("v.regla").alias("regla"),
        F.col("v.columna").alias("columna"),
        "sesion_id",
        "archivo_origen",
    )


def escribir_registro(df: DataFrame, ruta: str) -> None:
    """Agrega al registro (Delta). Se escribe aunque este vacio, para que
    la tabla exista desde la primera corrida y los consumidores (reporte,
    dashboard) no tengan que distinguir "sin incidencias" de "sin tabla"."""
    df.write.mode("append").format("delta").option("mergeSchema", "true").save(ruta)

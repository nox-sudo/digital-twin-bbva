"""
transform_silver.py

Pipeline Bronze -> Silver del Gemelo Digital Financiero.

Responsabilidad de este script:
  - Leer cada entidad desde Bronze (Delta Lake)
  - Aplicar tipado especifico (src/silver/typing_rules.py)
  - Aplicar deduplicacion basica (por llave primaria, se queda con el
    registro mas reciente segun _ingestion_timestamp de Bronze)
  - Validar contra las reglas declaradas en config/business_rules.yaml
    usando el motor generico (src/silver/validation.py)
  - Proteger la PII segun config/politica_pii.yaml (src/silver/pii.py):
    hash con sal, enmascarado o descarte. Se aplica DESPUES de validar
    (la validacion necesita los valores reales) y ANTES de escribir,
    tanto a filas validas como a cuarentena.
  - Escribir filas validas en Silver (Delta Lake)
  - Escribir filas rechazadas en cuarentena, con el motivo, sin detener
    el pipeline

Orden de procesamiento: respeta las dependencias de llave foranea
declaradas en business_rules.yaml (clientes antes que cuentas, cuentas
antes que cetes_inversiones y transacciones).

Uso:
    python transform_silver.py --bronze data/bronze --silver data/silver \
        --quarantine data/silver_quarantine --rules config/business_rules.yaml \
        --politica-pii config/politica_pii.yaml

Requiere el secreto PII_HASH_SALT (variable de entorno, o .env generado
por setup.sh).
"""

import argparse
import sys
from pathlib import Path

import yaml
from pyspark.sql import DataFrame, functions as F, Window

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.spark_session import get_spark_session  # noqa: E402
from src.common.logging_utils import PipelineRunLogger  # noqa: E402
from src.common.secretos import obtener_secreto  # noqa: E402
from src.silver.pii import columnas_en_claro, proteger_pii  # noqa: E402
from src.silver.validation import validate_entity  # noqa: E402
from src.silver.typing_rules import TYPING_REGISTRY  # noqa: E402

# Orden de procesamiento: entidades sin dependencias primero.
PROCESSING_ORDER = ["clientes", "cuentas", "cetes_inversiones", "transacciones"]

# Llave primaria por entidad, usada para deduplicar.
PRIMARY_KEYS = {
    "clientes": "cliente_id",
    "cuentas": "cuenta_id",
    "cetes_inversiones": "cuenta_id",  # cuentas tipo cetes son 1:1 con inversion
    "transacciones": "transaccion_id",
}


def deduplicate(df: DataFrame, primary_key: str) -> tuple[DataFrame, int]:
    """Se queda con el registro mas reciente por llave primaria,
    segun _ingestion_timestamp heredado de Bronze."""
    filas_antes = df.count()
    window = Window.partitionBy(primary_key).orderBy(
        F.col("_ingestion_timestamp").desc()
    )
    df = (
        df.withColumn("_rn", F.row_number().over(window))
        .filter(F.col("_rn") == 1)
        .drop("_rn")
    )
    filas_despues = df.count()
    return df, filas_antes - filas_despues


def process_entity(
    spark,
    entity_name: str,
    bronze_path: str,
    silver_path: str,
    quarantine_path: str,
    rules: dict,
    silver_context: dict,
    logger: PipelineRunLogger,
    politica_pii: dict,
    sal_pii: str,
) -> DataFrame:
    logger.start_entity(entity_name)

    try:
        df = spark.read.format("delta").load(f"{bronze_path}/{entity_name}")
    except Exception as e:
        # Se envuelve el error con el nombre de la entidad de forma
        # explicita, y se registra en el log ANTES de relanzar - sin
        # esto, el log final no deja rastro de que esta entidad
        # especifica fue la que hizo fallar la corrida.
        logger.end_entity(entity_name, estatus="FAILED", error=str(e))
        raise RuntimeError(
            f"[{entity_name}] No se pudo leer desde Bronze en "
            f"'{bronze_path}/{entity_name}'. Verifica que la ingesta "
            f"Bronze para esta entidad se haya completado antes de "
            f"correr Silver. Error original: {e}"
        ) from e

    typing_fn = TYPING_REGISTRY.get(entity_name)
    if typing_fn:
        df = typing_fn(df)

    df, duplicados_removidos = deduplicate(df, PRIMARY_KEYS[entity_name])

    df_valido, df_cuarentena, reporte = validate_entity(
        df, entity_name, rules.get(entity_name, {}), silver_context
    )

    politica = politica_pii.get(entity_name)
    df_valido = proteger_pii(df_valido, politica, sal_pii)
    df_cuarentena = proteger_pii(df_cuarentena, politica, sal_pii)
    # Verificacion final antes de escribir: ningun campo de la politica
    # puede llegar en claro. Si pasara (por ejemplo, un error al editar
    # la politica), es preferible detener el pipeline que escribirlo.
    for nombre, salida in (("silver", df_valido), ("cuarentena", df_cuarentena)):
        en_claro = columnas_en_claro(salida, politica)
        if en_claro:
            raise RuntimeError(
                f"[{entity_name}] PII en claro rumbo a {nombre}: {en_claro}"
            )

    (
        df_valido.write.mode("overwrite")
        .format("delta")
        .save(f"{silver_path}/{entity_name}")
    )

    if reporte.filas_cuarentena > 0:
        (
            df_cuarentena.write.mode("overwrite")
            .format("delta")
            .save(f"{quarantine_path}/{entity_name}")
        )

    logger.end_entity(
        entity_name,
        filas_entrada=reporte.filas_entrada,
        duplicados_removidos=duplicados_removidos,
        filas_validas=reporte.filas_validas,
        filas_cuarentena=reporte.filas_cuarentena,
        violaciones_por_regla=reporte.violaciones_por_regla,
    )

    return df_valido


def main():
    parser = argparse.ArgumentParser(description="Transformacion Bronze -> Silver")
    parser.add_argument("--bronze", default="data/bronze")
    parser.add_argument("--silver", default="data/silver")
    parser.add_argument("--quarantine", default="data/silver_quarantine")
    parser.add_argument("--rules", default="config/business_rules.yaml")
    parser.add_argument("--politica-pii", default="config/politica_pii.yaml")
    parser.add_argument("--logs", default="data/logs")
    args = parser.parse_args()

    with open(args.rules, encoding="utf-8") as f:
        rules = yaml.safe_load(f)
    with open(args.politica_pii, encoding="utf-8") as f:
        politica_pii = yaml.safe_load(f) or {}
    # Se lee antes de levantar Spark: si falta, falla en un segundo con
    # un mensaje claro, no despues de leer Bronze.
    sal_pii = obtener_secreto("PII_HASH_SALT")

    spark = get_spark_session("silver_transformation_gemelo_digital")
    logger = PipelineRunLogger(layer="silver", log_dir=args.logs)

    silver_context: dict[str, DataFrame] = {}
    status = "SUCCESS"
    try:
        for entity_name in PROCESSING_ORDER:
            df_valido = process_entity(
                spark,
                entity_name,
                args.bronze,
                args.silver,
                args.quarantine,
                rules,
                silver_context,
                logger,
                politica_pii,
                sal_pii,
            )
            silver_context[entity_name] = df_valido
    except Exception as e:
        status = f"FAILED: {e}"
        raise
    finally:
        logger.write(status=status)
        spark.stop()

    print("\n=== Transformacion Silver completada ===")


if __name__ == "__main__":
    main()

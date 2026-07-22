"""
ingest_bronze.py

Pipeline de ingesta Bronze - Gemelo Digital Financiero (BBVA / Tecmilenio)

Responsabilidad de este script (y SOLO esta):
  - Leer las fuentes crudas desde data/raw_sources/ (CSV, JSON)
  - Adjuntar metadata de ingesta (timestamp, archivo origen, formato original)
  - Persistir tal cual en la capa Bronze, sin limpiar ni transformar
    (eso es responsabilidad de Silver, no de aquí)

Principio clave del patrón Medallion: Bronze es inmutable y auditable.
Si algo llega "sucio" del origen, se queda sucio en Bronze a propósito -
es tu respaldo histórico de lo que realmente se recibió.

Uso:
    python ingest_bronze.py --source data/raw_sources --out bronze

Salida (Delta Lake, particionado por entidad):
    bronze/clientes/
    bronze/catalogo_productos/
    bronze/cuentas/
    bronze/cetes_inversiones/
    bronze/transacciones/   (particionado además por anio_mes de carga)
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from delta.pip_utils import configure_spark_with_delta_pip
from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def get_spark_session() -> SparkSession:
    # configure_spark_with_delta_pip descarga los JARs de Delta automáticamente,
    # pero las extensiones de catálogo deben declararse explícitamente en el builder.
    builder = (
        SparkSession.builder
        .appName("bronze_ingestion_gemelo_digital")
        .config("spark.sql.shuffle.partitions", "8")  # bajo para correr cómodo en local
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    )
    return configure_spark_with_delta_pip(builder).getOrCreate()


def con_metadata_ingesta(df, nombre_archivo_origen: str, formato_origen: str):
    """
    Adjunta las columnas de metadata que exige el kick-off:
    timestamp de ingesta y archivo fuente. Esto es lo que hace
    que Bronze sea trazable y auditable.
    """
    return (
        df
        .withColumn("_ingestion_timestamp", F.current_timestamp())
        .withColumn("_source_file", F.lit(nombre_archivo_origen))
        .withColumn("_source_format", F.lit(formato_origen))
    )


def ingest_clientes(spark: SparkSession, source_dir: Path, out_dir: Path):
    print("[Bronze] Ingiriendo clientes.csv ...")
    df = spark.read.csv(str(source_dir / "clientes.csv"), header=True, inferSchema=True)
    df = con_metadata_ingesta(df, "clientes.csv", "csv")
    (
        df.write.mode("overwrite").format("delta")
        .save(str(out_dir / "clientes"))
    )
    print(f"  -> {df.count()} filas escritas en {out_dir / 'clientes'}")


def ingest_catalogo_productos(spark: SparkSession, source_dir: Path, out_dir: Path):
    print("[Bronze] Ingiriendo catalogo_productos.json ...")
    # multiLine=True porque es un JSON anidado (no JSON-lines)
    df = spark.read.option("multiLine", True).json(str(source_dir / "catalogo_productos.json"))
    # El JSON trae un arreglo "productos" -> lo explotamos a filas
    df = df.select(F.explode("productos").alias("producto"))
    df = df.select("producto.*")
    df = con_metadata_ingesta(df, "catalogo_productos.json", "json")
    (
        df.write.mode("overwrite").format("delta")
        .save(str(out_dir / "catalogo_productos"))
    )
    print(f"  -> {df.count()} filas escritas en {out_dir / 'catalogo_productos'}")


def ingest_cuentas(spark: SparkSession, source_dir: Path, out_dir: Path):
    print("[Bronze] Ingiriendo cuentas.json ...")
    df = spark.read.option("multiLine", True).json(str(source_dir / "cuentas.json"))
    df = con_metadata_ingesta(df, "cuentas.json", "json")
    (
        df.write.mode("overwrite").format("delta")
        .save(str(out_dir / "cuentas"))
    )
    print(f"  -> {df.count()} filas escritas en {out_dir / 'cuentas'}")


def ingest_cetes(spark: SparkSession, source_dir: Path, out_dir: Path):
    print("[Bronze] Ingiriendo cetes_inversiones.csv ...")
    df = spark.read.csv(str(source_dir / "cetes_inversiones.csv"), header=True, inferSchema=True)
    df = con_metadata_ingesta(df, "cetes_inversiones.csv", "csv")
    (
        df.write.mode("overwrite").format("delta")
        .save(str(out_dir / "cetes_inversiones"))
    )
    print(f"  -> {df.count()} filas escritas en {out_dir / 'cetes_inversiones'}")


def ingest_transacciones(spark: SparkSession, source_dir: Path, out_dir: Path):
    """
    Las transacciones llegan en múltiples archivos (uno por mes),
    simulando cargas incrementales reales. Se leen todos juntos,
    pero se preserva el nombre del archivo origen por fila para
    trazabilidad, y se particiona la escritura por anio_mes.
    """
    print("[Bronze] Ingiriendo transacciones/ (múltiples archivos mensuales) ...")
    tx_dir = source_dir / "transacciones"
    archivos = sorted(tx_dir.glob("transacciones_*.csv"))

    if not archivos:
        raise FileNotFoundError(f"No se encontraron archivos de transacciones en {tx_dir}")

    df = (
        spark.read
        .option("header", True)
        .option("inferSchema", True)
        .csv(str(tx_dir / "transacciones_*.csv"))
        .withColumn("_source_file", F.input_file_name())
    )
    df = (
        df
        .withColumn("_ingestion_timestamp", F.current_timestamp())
        .withColumn("_source_format", F.lit("csv"))
        .withColumn("anio_mes", F.substring(F.col("fecha"), 1, 7))  # ej. "2026-07"
    )

    (
        df.write.mode("overwrite").format("delta")
        .partitionBy("anio_mes")
        .save(str(out_dir / "transacciones"))
    )
    print(f"  -> {df.count()} filas escritas en {out_dir / 'transacciones'} "
          f"(particionado por anio_mes, {len(archivos)} archivos origen)")


def escribir_log_ingesta(out_dir: Path, resumen: dict):
    """
    Log simple de la corrida - esto es el embrión de lo que después
    alimentará tu dashboard de observabilidad (Hito de calidad/monitoreo).
    """
    log_dir = out_dir.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"bronze_ingestion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, default=str)
    print(f"\n[Log] Resumen de ingesta guardado en {log_path}")


def main():
    parser = argparse.ArgumentParser(description="Ingesta Bronze - Gemelo Digital Financiero")
    parser.add_argument("--source", type=str, default="data/raw_sources", help="Carpeta de fuentes crudas")
    parser.add_argument("--out", type=str, default="bronze", help="Carpeta de salida Bronze")
    args = parser.parse_args()

    source_dir = Path(args.source)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not source_dir.exists():
        raise FileNotFoundError(
            f"No existe {source_dir}. Corre primero generate_synthetic_sources.py"
        )

    inicio = datetime.now()
    spark = get_spark_session()
    spark.sparkContext.setLogLevel("WARN")  # menos ruido en consola

    try:
        ingest_clientes(spark, source_dir, out_dir)
        ingest_catalogo_productos(spark, source_dir, out_dir)
        ingest_cuentas(spark, source_dir, out_dir)
        ingest_cetes(spark, source_dir, out_dir)
        ingest_transacciones(spark, source_dir, out_dir)

        estatus = "SUCCESS"
    except Exception as e:
        estatus = f"FAILED: {e}"
        raise
    finally:
        fin = datetime.now()
        escribir_log_ingesta(out_dir, {
            "inicio": inicio,
            "fin": fin,
            "duracion_segundos": (fin - inicio).total_seconds(),
            "estatus": estatus,
            "source_dir": str(source_dir),
            "out_dir": str(out_dir),
        })
        spark.stop()

    print("\n=== Ingesta Bronze completada ===")


if __name__ == "__main__":
    main()

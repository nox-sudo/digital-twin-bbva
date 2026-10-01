"""
quality_report.py

Reporte del registro historico de calidad (data/calidad/): que se
rechazo, por que regla, en que columna, y como cambia entre corridas;
mas las columnas con mas nulos. Lee solo el registro, que no contiene
valores de los datos (ver src/calidad/registro.py), asi que el reporte
nunca muestra PII.

Uso:
    python quality_report.py                 # ultima corrida + tendencia
    python quality_report.py --corridas 10   # tendencia de las ultimas 10
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pyspark.sql import functions as F  # noqa: E402

from src.common.spark_session import get_spark_session  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Reporte del registro de calidad")
    parser.add_argument("--calidad", default="data/calidad")
    parser.add_argument("--corridas", type=int, default=5)
    args = parser.parse_args()

    ruta_incidencias = Path(args.calidad) / "incidencias"
    ruta_perfil = Path(args.calidad) / "perfil_columnas"
    if not ruta_perfil.exists():
        print("Todavia no hay registro de calidad: corre primero transform_silver.py.")
        return

    spark = get_spark_session("reporte_calidad")
    spark.sparkContext.setLogLevel("ERROR")
    perfil = spark.read.format("delta").load(str(ruta_perfil))
    incid = spark.read.format("delta").load(str(ruta_incidencias))

    corridas = [
        f.run_id
        for f in perfil.select("run_id").distinct().orderBy(F.desc("run_id")).collect()
    ][: args.corridas]
    ultima = corridas[0]

    print(f"\n=== Ultima corrida: {ultima} ===")
    print("\nFilas y filas rechazadas por entidad:")
    filas = (
        perfil.filter(F.col("run_id") == ultima)
        .groupBy("entidad")
        .agg(F.max("filas").alias("filas"))
    )
    rechazadas = (
        incid.filter(F.col("run_id") == ultima)
        .groupBy("entidad")
        .agg(F.countDistinct("llave").alias("rechazadas"))
    )
    (
        filas.join(rechazadas, "entidad", "left")
        .fillna(0, ["rechazadas"])
        .withColumn(
            "pct_rechazo", F.round(F.col("rechazadas") / F.col("filas") * 100, 2)
        )
        .orderBy("entidad")
        .show(truncate=False)
    )

    print("Incidencias por regla y columna:")
    (
        incid.filter(F.col("run_id") == ultima)
        .groupBy("entidad", "regla", "columna")
        .count()
        .orderBy(F.desc("count"))
        .show(50, truncate=False)
    )

    print("Columnas con nulos (incluye opcionales):")
    (
        perfil.filter((F.col("run_id") == ultima) & (F.col("nulos") > 0))
        .withColumn("pct_nulos", F.round(F.col("nulos") / F.col("filas") * 100, 2))
        .select("entidad", "columna", "nulos", "pct_nulos")
        .orderBy(F.desc("pct_nulos"))
        .show(50, truncate=False)
    )

    print(f"=== Tendencia: incidencias por corrida (ultimas {len(corridas)}) ===")
    # Se parte de las corridas del perfil (todas tienen perfil) para que
    # una corrida limpia aparezca con 0, en vez de no aparecer.
    todas = perfil.filter(F.col("run_id").isin(corridas)).select("run_id").distinct()
    conteos = (
        incid.filter(F.col("run_id").isin(corridas))
        .groupBy("run_id")
        .agg(
            F.count(F.lit(1)).alias("incidencias"),
            F.countDistinct("entidad", "llave").alias("filas_rechazadas"),
        )
    )
    (
        todas.join(conteos, "run_id", "left")
        .fillna(0, ["incidencias", "filas_rechazadas"])
        .orderBy("run_id")
        .show(truncate=False)
    )
    spark.stop()


if __name__ == "__main__":
    main()

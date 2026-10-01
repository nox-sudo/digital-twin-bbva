"""
ingest_bronze.py

Pipeline de ingesta Bronze - Gemelo Digital Financiero (BBVA / Tecmilenio)

Responsabilidad de este script (y SOLO esta):
  - Tomar entregas de datos de origen: una carpeta local (una sesion,
    por default data/raw_sources/) o la zona de aterrizaje en MinIO
    (--desde-landing), descargando y verificando cada entrega.
  - Ingerir SOLO los archivos que no se hayan ingerido antes (registro
    de control por ruta + SHA-256, ver src/bronze/control.py).
  - Adjuntar metadata de trazabilidad (timestamp, archivo, formato,
    sesion/entrega de origen).
  - Agregar (append) a la capa Bronze, sin limpiar ni transformar.

Principio clave del patron Medallion: Bronze es inmutable y auditable.
Si algo llega "sucio" del origen, se queda sucio en Bronze a proposito.
Y es acumulativo: cada version de clientes.csv que llego queda en Bronze
con su timestamp; Silver se queda con la mas reciente por llave.

Uso:
    python ingest_bronze.py --source data/raw_sources --out data/bronze
    python ingest_bronze.py --desde-landing --out data/bronze

Salida (Delta Lake):
    data/bronze/clientes/, catalogo_productos/, cuentas/, cetes_inversiones/
    data/bronze/transacciones/   (particionado por anio_mes)
    data/bronze/_control_ingesta.jsonl
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from delta.pip_utils import configure_spark_with_delta_pip
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType, StringType, StructField, StructType

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.bronze.control import (  # noqa: E402
    ArchivoPendiente,
    Entrega,
    anotar_ingeridos,
    bronze_sin_control,
    leer_control,
    planear_ingesta,
)
from src.common.landing import (  # noqa: E402
    cliente_s3,
    descargar_entrega,
    listar_entregas,
)
from src.common.sesiones import checksum_archivo  # noqa: E402

# Bronze es inmutable y sin tipar a proposito: cada campo de negocio se
# lee como StringType, sin importar como se vea en el JSON de origen.
# inferSchema no es una opcion real para el lector JSON de Spark (se
# ignora en silencio, verificado empiricamente) - por eso el unico
# control real de tipo para JSON es un StructType explicito.
SCHEMA_CUENTAS = StructType(
    [
        StructField("cuenta_id", StringType(), True),
        StructField("cliente_id", StringType(), True),
        StructField("tipo_cuenta", StringType(), True),
        StructField("fecha_apertura", StringType(), True),
        StructField("saldo_actual", StringType(), True),
        StructField("moneda", StringType(), True),
        StructField("estatus", StringType(), True),
        StructField("limite_credito", StringType(), True),
        StructField("monto_original", StringType(), True),
        StructField("plazo_meses", StringType(), True),
    ]
)

# plazos_meses y plazos_dias son listas en el JSON de origen (los
# plazos que el producto ofrece en general, ej. [12, 24, 36, 48]) - no
# confundir con plazo_meses (singular, escalar) de cuentas, que es el
# plazo de una cuenta especifica. Se preservan como ArrayType para no
# perder la estructura, pero cada elemento queda como string, sin
# inferencia numerica.
SCHEMA_CATALOGO_PRODUCTOS = StructType(
    [
        StructField(
            "productos",
            ArrayType(
                StructType(
                    [
                        StructField("producto_id", StringType(), True),
                        StructField("nombre", StringType(), True),
                        StructField("tasa_interes_anual", StringType(), True),
                        StructField("requiere_ingreso_minimo", StringType(), True),
                        StructField("limite_credito_min", StringType(), True),
                        StructField("limite_credito_max", StringType(), True),
                        StructField("plazos_meses", ArrayType(StringType()), True),
                        StructField("plazos_dias", ArrayType(StringType()), True),
                    ]
                )
            ),
            True,
        )
    ]
)


def get_spark_session() -> SparkSession:
    # configure_spark_with_delta_pip descarga los JARs de Delta automáticamente,
    # pero las extensiones de catálogo deben declararse explícitamente en el builder.
    builder = (
        SparkSession.builder.appName("bronze_ingestion_gemelo_digital")
        .config("spark.sql.shuffle.partitions", "8")  # bajo para correr cómodo en local
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
    )
    return configure_spark_with_delta_pip(builder).getOrCreate()


def con_metadata_ingesta(df, formato_origen: str, entrega_id: str):
    """
    Adjunta las columnas de metadata que exige el kick-off: timestamp de
    ingesta y archivo fuente. Esto es lo que hace que Bronze sea
    trazable y auditable. _sesion_id liga cada fila con la sesion (la
    entrega) que la trajo, y por lo tanto con los parametros que la
    generaron.

    _source_file se toma de input_file_name() (que trae la ruta completa,
    ej. file:///opt/lakehouse/data/.../transacciones_2026_06.csv) y se
    reduce al nombre de archivo, igual para todas las entidades.
    """
    return (
        df.withColumn("_ingestion_timestamp", F.current_timestamp())
        .withColumn(
            "_source_file", F.regexp_extract(F.input_file_name(), r"([^/\\]+)$", 1)
        )
        .withColumn("_source_format", F.lit(formato_origen))
        .withColumn("_sesion_id", F.lit(entrega_id))
    )


def leer_entidad(spark: SparkSession, entidad: str, rutas: list[Path]):
    """Lee los archivos de una entidad como texto (sin inferir tipos) y
    devuelve el DataFrame con su formato de origen."""
    rutas_str = [str(r) for r in rutas]
    if entidad == "catalogo_productos":
        # multiLine porque es un JSON anidado (no JSON-lines); el arreglo
        # "productos" se explota a una fila por producto.
        df = (
            spark.read.option("multiLine", True)
            .schema(SCHEMA_CATALOGO_PRODUCTOS)
            .json(rutas_str)
            .select(F.explode("productos").alias("producto"))
            .select("producto.*")
        )
        return df, "json"
    if entidad == "cuentas":
        df = spark.read.option("multiLine", True).schema(SCHEMA_CUENTAS).json(rutas_str)
        return df, "json"
    # clientes, cetes_inversiones, transacciones: CSV como texto.
    df = spark.read.option("header", True).option("inferSchema", False).csv(rutas_str)
    return df, "csv"


def escribir_bronze(df, entidad: str, out_dir: Path) -> None:
    """Agrega a la tabla Delta de la entidad.

    mergeSchema: si una entrega trae columnas nuevas (por ejemplo, la PII
    agregada a clientes), Delta extiende el esquema de la tabla en vez de
    rechazar la escritura; las filas anteriores quedan con NULL en esas
    columnas. Es la evolucion de esquema que se espera en una zona cruda.
    """
    escritor = df.write.mode("append").format("delta").option("mergeSchema", "true")
    if entidad == "transacciones":
        escritor = escritor.partitionBy("anio_mes")
    escritor.save(str(out_dir / entidad))


def entrega_local(source_dir: Path) -> Entrega:
    """Una carpeta local como entrega. Si trae manifest (sesion del
    generador), se usan su id y sus checksums; si no, se calculan."""
    manifest_path = source_dir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        return Entrega(manifest["sesion_id"], source_dir, manifest["archivos"])
    archivos = {
        str(p.relative_to(source_dir)): checksum_archivo(p)
        for p in sorted(source_dir.rglob("*"))
        if p.is_file()
    }
    return Entrega("local", source_dir, archivos)


def entregas_desde_landing(staging: Path, ingeridos: set) -> list[Entrega]:
    """Descarga (verificando checksums) las entregas de la landing zone
    que tengan algo sin ingerir."""
    s3 = cliente_s3()
    entregas = []
    for manifest in listar_entregas(s3):
        pendiente = any(
            (ruta, sha) not in ingeridos for ruta, sha in manifest["archivos"].items()
        )
        if not pendiente:
            continue
        print(
            f"[Bronze] Descargando entrega {manifest['entrega_id']} desde la landing zone"
        )
        carpeta = descargar_entrega(s3, manifest, staging)
        entregas.append(Entrega(manifest["entrega_id"], carpeta, manifest["archivos"]))
    return entregas


def ingerir(spark, pendientes: list[ArchivoPendiente], out_dir: Path) -> dict:
    """Ingiere los pendientes, agrupados por entrega y entidad (una
    escritura Delta por grupo). Anota cada grupo en el registro de
    control en cuanto Spark confirma su escritura."""
    resumen = {}
    grupos: dict[tuple[str, str], list[ArchivoPendiente]] = {}
    for p in pendientes:
        grupos.setdefault((p.entrega_id, p.entidad), []).append(p)

    for (entrega_id, entidad), archivos in grupos.items():
        rutas = [a.carpeta / a.ruta for a in archivos]
        df, formato = leer_entidad(spark, entidad, rutas)
        df = con_metadata_ingesta(df, formato, entrega_id)
        if entidad == "transacciones":
            df = df.withColumn("anio_mes", F.substring(F.col("fecha"), 1, 7))
        df = df.cache()
        filas_por_archivo = {
            fila["_source_file"]: fila["count"]
            for fila in df.groupBy("_source_file").count().collect()
        }
        escribir_bronze(df, entidad, out_dir)
        df.unpersist()
        anotar_ingeridos(out_dir, archivos, filas_por_archivo)

        filas = sum(filas_por_archivo.values())
        resumen.setdefault(entidad, 0)
        resumen[entidad] += filas
        print(
            f"[Bronze] {entrega_id} / {entidad}: {len(archivos)} archivo(s), {filas} filas"
        )
    return resumen


def escribir_log_ingesta(out_dir: Path, resumen: dict):
    """
    Log simple de la corrida - esto es el embrion de lo que despues
    alimenta el dashboard de observabilidad (Hito de calidad/monitoreo).
    """
    log_dir = out_dir.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = (
        log_dir / f"bronze_ingestion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, default=str)
    print(f"\n[Log] Resumen de ingesta guardado en {log_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Ingesta Bronze incremental - Gemelo Digital Financiero"
    )
    parser.add_argument(
        "--source", default="data/raw_sources", help="Carpeta local de fuentes"
    )
    parser.add_argument(
        "--desde-landing",
        action="store_true",
        help="Tomar las entregas de la landing zone en MinIO en vez de --source",
    )
    parser.add_argument("--staging", default="data/staging")
    parser.add_argument("--out", default="data/bronze", help="Carpeta de salida Bronze")
    args = parser.parse_args()

    out_dir = Path(args.out)
    if bronze_sin_control(out_dir):
        raise RuntimeError(
            f"{out_dir} tiene datos de la version anterior (sin registro de control). "
            "Bronze se reconstruye completo desde las fuentes: borra esa carpeta "
            "(o usa 'bash demo.sh limpiar') y vuelve a correr."
        )
    ingeridos = leer_control(out_dir)

    if args.desde_landing:
        entregas = entregas_desde_landing(Path(args.staging), ingeridos)
        origen = "landing"
    else:
        source_dir = Path(args.source)
        if not source_dir.exists():
            raise FileNotFoundError(
                f"No existe {source_dir}. Corre primero generate_synthetic_sources.py"
            )
        entregas = [entrega_local(source_dir)]
        origen = str(source_dir)

    pendientes = planear_ingesta(entregas, ingeridos)
    inicio = datetime.now()
    resumen = {}
    estatus = "SUCCESS"

    if not pendientes:
        print("[Bronze] No hay archivos nuevos: todo lo recibido ya estaba ingerido.")
    else:
        print(
            f"[Bronze] {len(pendientes)} archivo(s) nuevo(s) de {len(entregas)} entrega(s)"
        )
        spark = get_spark_session()
        spark.sparkContext.setLogLevel("WARN")
        try:
            resumen = ingerir(spark, pendientes, out_dir)
        except Exception as e:
            estatus = f"FAILED: {e}"
            raise
        finally:
            spark.stop()
            fin = datetime.now()
            escribir_log_ingesta(
                out_dir,
                {
                    "inicio": inicio,
                    "fin": fin,
                    "duracion_segundos": (fin - inicio).total_seconds(),
                    "estatus": estatus,
                    "origen": origen,
                    "entregas": [e.entrega_id for e in entregas],
                    "archivos_ingeridos": len(pendientes),
                    "filas_por_entidad": resumen,
                    "out_dir": str(out_dir),
                },
            )

    print("\n=== Ingesta Bronze completada ===")


if __name__ == "__main__":
    main()

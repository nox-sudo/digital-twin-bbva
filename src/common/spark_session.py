"""
src/common/spark_session.py

SparkSession compartido para Bronze, Silver y Gold. Centraliza la
configuracion de Delta Lake para no repetirla en cada script (como
pasaba antes: cada capa tenia su propia copia de estas lineas).

Docker-ready: el master de Spark se lee de una variable de entorno,
con "local[*]" como default para desarrollo fuera de contenedor. Cuando
esto corra dentro de Docker Compose con un cluster Spark real, solo se
necesita definir SPARK_MASTER_URL en el entorno del contenedor, sin
tocar este archivo ni el codigo que lo usa.
"""

import os

from pyspark.sql import SparkSession


def get_spark_session(app_name: str) -> SparkSession:
    """Crea una SparkSession con Delta Lake configurado.

    Lee SPARK_MASTER_URL del entorno (default: local[*]), para que el
    mismo codigo corra igual en local y dentro de un contenedor Docker
    apuntando a un cluster Spark real, sin cambios de codigo.
    """
    master_url = os.environ.get("SPARK_MASTER_URL", "local[*]")
    shuffle_partitions = os.environ.get("SPARK_SHUFFLE_PARTITIONS", "8")

    builder = (
        SparkSession.builder.appName(app_name)
        .master(master_url)
        .config("spark.sql.shuffle.partitions", shuffle_partitions)
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
    )

    try:
        from delta import configure_spark_with_delta_pip

        builder = configure_spark_with_delta_pip(builder)
    except ImportError:
        pass  # delta-spark no instalado; asume que los JARs ya estan en el classpath (caso Docker)

    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel(os.environ.get("SPARK_LOG_LEVEL", "WARN"))
    return spark


def get_data_path(layer: str) -> str:
    """Resuelve la ruta de una capa del Lakehouse (bronze/silver/gold).

    Lee DATA_ROOT del entorno (default: "data"), para que las rutas
    cambien limpiamente entre ejecucion local y un volumen montado en
    Docker (ej. DATA_ROOT=/opt/lakehouse/data), sin tocar codigo.
    """
    root = os.environ.get("DATA_ROOT", "data")
    return f"{root}/{layer}"

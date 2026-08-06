"""
tests/conftest.py

Fixture compartido de SparkSession para los tests. No usa Delta Lake
deliberadamente: el motor de validacion (src/silver/validation.py)
opera sobre DataFrames de PySpark genericos, sin importar el formato
de almacenamiento subyacente. Probarlo sin Delta hace los tests mas
rapidos y evita depender de descargar JARs en el entorno de CI.
"""

import pytest
from pyspark.sql import SparkSession


@pytest.fixture(scope="session")
def spark():
    session = (
        SparkSession.builder.appName("tests_gemelo_digital_financiero")
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()

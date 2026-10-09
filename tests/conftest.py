"""
tests/conftest.py

Fixture compartido de SparkSession para los tests. No usa Delta Lake
deliberadamente: el motor de validacion (src/silver/validation.py)
opera sobre DataFrames de PySpark genericos, sin importar el formato
de almacenamiento subyacente. Probarlo sin Delta hace los tests mas
rapidos y evita depender de descargar JARs en el entorno de CI.
"""

import os
import sys

import pytest
from pyspark.sql import SparkSession

# Los workers de Spark lanzan "python3" del PATH. Si pytest se corre sin el
# venv activo (por ejemplo .venv/bin/python -m pytest), ese python3 es el del
# sistema, no tiene pyspark y los tests de Spark fallan con "Error from python
# worker". Con esto los workers usan el mismo interprete que corre pytest.
os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)


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

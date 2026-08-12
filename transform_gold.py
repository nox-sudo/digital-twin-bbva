"""
transform_gold.py

Pipeline Silver -> Gold del Gemelo Digital Financiero.

Lee las entidades limpias de Silver (Delta Lake), calcula los 12 KPIs
del catalogo (config/kpi_catalog.yaml, logica en
src/gold/kpi_definitions.py), y los escribe en una tabla normalizada
en DuckDB.

Formato normalizado (largo, no ancho): una fila por (cliente_id,
kpi_id), no una columna por KPI. Esto permite agregar un KPI nuevo sin
alterar el esquema de la tabla de destino.

Uso:
    python transform_gold.py --silver data/silver --out data/gold/kpis.duckdb \
        --catalog config/kpi_catalog.yaml
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.spark_session import get_spark_session  # noqa: E402
from src.common.logging_utils import PipelineRunLogger  # noqa: E402
from src.gold.kpi_definitions import KPI_REGISTRY  # noqa: E402


def cargar_silver_context(spark, silver_path: str) -> dict:
    entidades = ["clientes", "cuentas", "cetes_inversiones", "transacciones"]
    ctx = {}
    for entidad in entidades:
        ctx[entidad] = spark.read.format("delta").load(f"{silver_path}/{entidad}")
    return ctx


def calcular_kpi(kpi_id: str, metadata: dict, silver_context: dict) -> pd.DataFrame:
    """Corre la funcion de calculo del registro y le agrega la
    metadata del catalogo (categoria, nombre, timestamp)."""
    if kpi_id not in KPI_REGISTRY:
        raise ValueError(
            f"El KPI '{kpi_id}' esta declarado en kpi_catalog.yaml pero no "
            f"tiene funcion de calculo en KPI_REGISTRY (src/gold/kpi_definitions.py)."
        )

    resultado = KPI_REGISTRY[kpi_id](silver_context)

    if "valor_numerico" not in resultado.columns:
        resultado["valor_numerico"] = None
    if "valor_texto" not in resultado.columns:
        resultado["valor_texto"] = None

    resultado["kpi_id"] = kpi_id
    resultado["categoria"] = metadata["categoria"]
    resultado["nombre"] = metadata["nombre"]
    resultado["fecha_calculo"] = datetime.now()

    return resultado[
        [
            "cliente_id",
            "kpi_id",
            "categoria",
            "nombre",
            "valor_numerico",
            "valor_texto",
            "fecha_calculo",
        ]
    ]


def main():
    parser = argparse.ArgumentParser(description="Transformacion Silver -> Gold (KPIs)")
    parser.add_argument("--silver", default="data/silver")
    parser.add_argument("--out", default="data/gold/kpis.duckdb")
    parser.add_argument("--catalog", default="config/kpi_catalog.yaml")
    parser.add_argument("--logs", default="data/logs")
    args = parser.parse_args()

    with open(args.catalog, encoding="utf-8") as f:
        catalogo = yaml.safe_load(f)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    spark = get_spark_session("gold_kpis_gemelo_digital")
    logger = PipelineRunLogger(layer="gold", log_dir=args.logs)

    status = "SUCCESS"
    try:
        silver_context = cargar_silver_context(spark, args.silver)

        resultados = []
        for kpi_id, metadata in catalogo.items():
            logger.start_entity(kpi_id)
            df_kpi = calcular_kpi(kpi_id, metadata, silver_context)
            resultados.append(df_kpi)

            pendiente = metadata.get("pendiente", False)
            logger.end_entity(
                kpi_id,
                filas=len(df_kpi),
                clientes_con_valor=int(
                    df_kpi["valor_numerico"].notna().sum()
                    + df_kpi["valor_texto"].notna().sum()
                ),
                pendiente=pendiente,
            )

        # DuckDB referencia esta variable por nombre dentro del SQL de abajo
        # (replacement scan sobre el frame de Python) - no es un uso normal
        # de Python, asi que flake8 no lo detecta y la marca como no usada.
        tabla_final = pd.concat(resultados, ignore_index=True)  # noqa: F841

        con = duckdb.connect(args.out)
        con.execute("DROP TABLE IF EXISTS gold_kpis")
        con.execute("CREATE TABLE gold_kpis AS SELECT * FROM tabla_final")
        total_filas = con.execute("SELECT COUNT(*) FROM gold_kpis").fetchone()[0]
        con.close()

        print(
            f"\nGold escrito en {args.out}: {total_filas} filas "
            f"({len(catalogo)} KPIs x clientes)"
        )

    except Exception as e:
        status = f"FAILED: {e}"
        raise
    finally:
        logger.write(status=status)
        spark.stop()

    print("\n=== Transformacion Gold completada ===")


if __name__ == "__main__":
    main()

"""
build_features.py

Construye la tabla de features por cliente (gold_features_cliente) y la
persiste en el DuckDB de Gold, junto a gold_kpis. Es el feature store
ligero del proyecto: la fuente unica de features para el modelo de
riesgo, el dashboard, el simulador y el asistente RAG.

Corre despues de transform_gold.py, porque parte de los KPIs de Gold, y
antes de train_model.py / predict_risk.py, que la leen. Detalle del
diseno en src/gold/risk_features.py.

Uso:
    python build_features.py --silver data/silver --gold data/gold/kpis.duckdb
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.spark_session import get_spark_session  # noqa: E402
from src.gold.risk_features import (  # noqa: E402
    TABLA_FEATURES,
    construir_features,
    escribir_features,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description=f"Construye {TABLA_FEATURES} en el DuckDB de Gold"
    )
    parser.add_argument("--silver", default="data/silver")
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    args = parser.parse_args()

    spark = get_spark_session("construir_features")
    try:
        features = construir_features(spark, args.silver, args.gold)
    except Exception:
        logger.exception("Fallo la construccion de features")
        raise
    finally:
        spark.stop()

    escribir_features(features, args.gold)


if __name__ == "__main__":
    main()

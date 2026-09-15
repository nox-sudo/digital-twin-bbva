"""
predict_risk.py

Predice la probabilidad de impago para todos los clientes con el
modelo entrenado por train_model.py, y escribe el resultado de vuelta
en dos lugares: como Parquet en data/gold/predicciones/, y
reemplazando las filas de probabilidad_impago en la tabla gold_kpis
de DuckDB, que hasta ahora tenian valor NULL a proposito (ver
src/gold/kpi_definitions.py, funcion kpi_probabilidad_impago).

Usa exactamente las mismas features que train_model.py
(src/gold/risk_features.py), para que no haya desalineacion entre lo
que el modelo aprendio y lo que recibe en inferencia.

Uso:
    python predict_risk.py --silver data/silver --gold data/gold/kpis.duckdb \
        --model models/risk_model.joblib --out data/gold/predicciones
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import joblib
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.spark_session import get_spark_session  # noqa: E402
from src.gold.risk_features import construir_features  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def predecir(modelo, features: pd.DataFrame) -> pd.DataFrame:
    """Calcula probabilidad_impago para cada cliente en features."""
    probabilidades = modelo.predict_proba(features)[:, 1]
    return pd.DataFrame(
        {"cliente_id": features.index, "valor_numerico": probabilidades}
    )


def actualizar_gold(gold_duckdb_path: str, predicciones: pd.DataFrame) -> None:
    """Reemplaza las filas de probabilidad_impago en gold_kpis con
    los valores reales del modelo, en vez de los NULL que
    kpi_probabilidad_impago escribia mientras el modelo no existia."""
    tabla_actualizada = predicciones.copy()
    tabla_actualizada["kpi_id"] = "probabilidad_impago"
    tabla_actualizada["categoria"] = "riesgo_y_endeudamiento"
    tabla_actualizada["nombre"] = "Probabilidad de impago"
    tabla_actualizada["valor_texto"] = None
    tabla_actualizada["fecha_calculo"] = datetime.now()
    tabla_actualizada = tabla_actualizada[
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

    con = duckdb.connect(gold_duckdb_path)
    try:
        con.execute("DELETE FROM gold_kpis WHERE kpi_id = 'probabilidad_impago'")
        con.execute("INSERT INTO gold_kpis SELECT * FROM tabla_actualizada")
        n_filas = con.execute(
            "SELECT COUNT(*) FROM gold_kpis WHERE kpi_id = 'probabilidad_impago'"
        ).fetchone()[0]
        logger.info(
            "gold_kpis actualizado: %d filas de probabilidad_impago con valor real",
            n_filas,
        )
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(
        description="Predice probabilidad de impago por cliente"
    )
    parser.add_argument("--silver", default="data/silver")
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument("--model", default="models/risk_model.joblib")
    parser.add_argument("--out", default="data/gold/predicciones")
    args = parser.parse_args()

    modelo = joblib.load(args.model)
    logger.info("Modelo cargado desde %s", args.model)

    spark = get_spark_session("predecir_riesgo")
    try:
        features = construir_features(spark, args.silver, args.gold)
        predicciones = predecir(modelo, features)

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "probabilidad_impago.parquet"
        predicciones.to_parquet(out_path, index=False)
        logger.info(
            "Predicciones escritas en %s: %d clientes", out_path, len(predicciones)
        )

        actualizar_gold(args.gold, predicciones)

        promedio = predicciones["valor_numerico"].mean()
        logger.info(
            "Prediccion completada: %d clientes, probabilidad promedio de impago = %.4f",
            len(predicciones),
            promedio,
        )
    except Exception:
        logger.exception("Fallo la prediccion de riesgo")
        raise
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

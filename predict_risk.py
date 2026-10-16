"""
predict_risk.py

Predice la probabilidad de impago para todos los clientes con el
modelo entrenado por train_model.py, y escribe el resultado de vuelta
en dos lugares: como Parquet en data/gold/predicciones/, y
reemplazando las filas de probabilidad_impago en la tabla gold_kpis
de DuckDB, que hasta ahora tenian valor NULL a proposito (ver
src/gold/kpi_definitions.py, funcion kpi_probabilidad_impago).

Lee las mismas features que train_model.py (tabla gold_features_cliente)
y las alinea a las columnas exactas con las que se entreno el modelo
(feature_names_in_), para que no haya desalineacion entre lo que el
modelo aprendio y lo que recibe en inferencia. No necesita Spark.

Uso:
    python predict_risk.py --gold data/gold/kpis.duckdb \
        --model models/risk_model.joblib --out data/gold/predicciones
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import joblib
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.gold.risk_features import (  # noqa: E402
    cargar_features,
    preparar_para_modelo,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def predecir(
    modelo, features: pd.DataFrame, fuera_de_pliegue: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Calcula probabilidad_impago para cada cliente en features.

    Un cliente con probabilidad fuera de pliegue (train_model.py) recibe
    esa: el modelo final se entreno con su etiqueta, y predecirlo con el
    daria un valor sobreajustado. El modelo final solo se usa para clientes
    que no participaron en el entrenamiento (por ejemplo, altas nuevas)."""
    probabilidades = pd.Series(
        modelo.predict_proba(features)[:, 1], index=features.index
    )
    origen = pd.Series("modelo_final", index=features.index)
    if fuera_de_pliegue is not None:
        oof = fuera_de_pliegue.set_index("cliente_id")["probabilidad"]
        comunes = probabilidades.index.intersection(oof.index)
        probabilidades.loc[comunes] = oof.loc[comunes]
        origen.loc[comunes] = "fuera_de_pliegue"
    logger.info(
        "Probabilidades: %d fuera de pliegue, %d del modelo final",
        int((origen == "fuera_de_pliegue").sum()),
        int((origen == "modelo_final").sum()),
    )
    return pd.DataFrame(
        {"cliente_id": features.index, "valor_numerico": probabilidades.values}
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

    # DELETE e INSERT van en una sola transaccion: si el INSERT falla, el
    # ROLLBACK deja gold_kpis exactamente como estaba. Sin ella, un fallo
    # entre los dos pasos dejaba a todos los clientes sin probabilidad_impago.
    con = duckdb.connect(gold_duckdb_path)
    try:
        con.register("tabla_actualizada", tabla_actualizada)
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM gold_kpis WHERE kpi_id = 'probabilidad_impago'")
            con.execute("INSERT INTO gold_kpis SELECT * FROM tabla_actualizada")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        n_filas = con.execute(
            "SELECT COUNT(*) FROM gold_kpis WHERE kpi_id = 'probabilidad_impago'"
        ).fetchone()[0]
        logger.info(
            "gold_kpis actualizado: %d filas de probabilidad_impago con valor real",
            n_filas,
        )
    finally:
        con.close()


def escribir_parquet_atomico(predicciones: pd.DataFrame, out_path: Path) -> None:
    """Escribe a un archivo temporal en la misma carpeta y lo renombra.
    os.replace es atomico dentro del mismo sistema de archivos: quien lea
    el Parquet ve la version anterior completa o la nueva completa, nunca
    un archivo a medio escribir."""
    temporal = out_path.with_suffix(".parquet.tmp")
    predicciones.to_parquet(temporal, index=False)
    os.replace(temporal, out_path)


def main():
    parser = argparse.ArgumentParser(
        description="Predice probabilidad de impago por cliente"
    )
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument("--model", default="models/risk_model.joblib")
    parser.add_argument("--out", default="data/gold/predicciones")
    parser.add_argument("--oof", default="models/probabilidad_fuera_de_pliegue.parquet")
    args = parser.parse_args()

    modelo = joblib.load(args.model)
    logger.info("Modelo cargado desde %s", args.model)

    try:
        features = preparar_para_modelo(
            cargar_features(args.gold), columnas_modelo=modelo.feature_names_in_
        )
        ruta_oof = Path(args.oof)
        fuera_de_pliegue = pd.read_parquet(ruta_oof) if ruta_oof.exists() else None
        if fuera_de_pliegue is None:
            logger.warning(
                "No existe %s: todos los clientes se predicen con el modelo final "
                "(sobreajustado para los que tienen etiqueta). Corre train_model.py.",
                ruta_oof,
            )
        predicciones = predecir(modelo, features, fuera_de_pliegue)

        # DuckDB primero: es la fuente que leen el dashboard y el asistente.
        # El Parquet es la copia de exportacion y solo se escribe si Gold
        # ya quedo actualizado, para que nunca vaya adelante de DuckDB.
        actualizar_gold(args.gold, predicciones)

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "probabilidad_impago.parquet"
        escribir_parquet_atomico(predicciones, out_path)
        logger.info(
            "Predicciones escritas en %s: %d clientes", out_path, len(predicciones)
        )

        promedio = predicciones["valor_numerico"].mean()
        logger.info(
            "Prediccion completada: %d clientes, probabilidad promedio de impago = %.4f",
            len(predicciones),
            promedio,
        )
    except Exception:
        logger.exception("Fallo la prediccion de riesgo")
        raise


if __name__ == "__main__":
    main()

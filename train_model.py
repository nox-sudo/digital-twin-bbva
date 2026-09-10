"""
train_model.py

Entrena el modelo de clasificacion de riesgo crediticio (probabilidad
de impago), el ultimo KPI pendiente del catalogo de Gold.

Combina los 11 KPIs de Gold (todos menos probabilidad_impago, que es
el objetivo) con variables de comportamiento de Silver
(src/gold/risk_features.py, compartido con predict_risk.py para que
entrenamiento e inferencia nunca usen features distintas), y las
etiquetas sinteticas generadas por generate_labels.py.

Uso:
    python train_model.py --silver data/silver --gold data/gold/kpis.duckdb \
        --labels data/labels/risk_labels.parquet \
        --model-out models/risk_model.joblib
"""

import argparse
import logging
import sys
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import shap  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    classification_report,
    confusion_matrix,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split  # noqa: E402
from xgboost import XGBClassifier  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.spark_session import get_spark_session  # noqa: E402
from src.gold.risk_features import construir_features  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def entrenar(
    features: pd.DataFrame, etiquetas: pd.DataFrame, model_out: Path, shap_out: Path
):
    """Entrena XGBoost, evalua contra el conjunto de prueba, genera
    el grafico SHAP, y guarda el modelo. Retorna las metricas para el
    reporte final."""
    dataset = features.join(etiquetas.set_index("cliente_id")["label"], how="inner")
    logger.info(
        "Dataset de entrenamiento: %d clientes con features y etiqueta", len(dataset)
    )

    x = dataset.drop(columns=["label"])
    y = dataset["label"]

    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=0.2, stratify=y, random_state=42
    )
    logger.info(
        "Split 80/20: %d train (%d positivos), %d test (%d positivos)",
        len(x_train),
        int(y_train.sum()),
        len(x_test),
        int(y_test.sum()),
    )

    positivos = int(y_train.sum())
    negativos = len(y_train) - positivos
    peso_positivo = negativos / positivos if positivos else 1.0

    modelo = XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=peso_positivo,
        eval_metric="logloss",
        random_state=42,
    )
    modelo.fit(x_train, y_train)

    probabilidades = modelo.predict_proba(x_test)[:, 1]
    predicciones = (probabilidades >= 0.5).astype(int)

    auc_roc = roc_auc_score(y_test, probabilidades)
    matriz_confusion = confusion_matrix(y_test, predicciones)
    reporte_clasificacion = classification_report(
        y_test, predicciones, output_dict=True
    )

    model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(modelo, model_out)
    logger.info("Modelo guardado en %s", model_out)

    explainer = shap.TreeExplainer(modelo)
    valores_shap = explainer.shap_values(x_test)
    # Versiones distintas de shap devuelven una lista [clase_0, clase_1]
    # para clasificacion binaria, o un solo array ya para la clase
    # positiva; se cubre cualquiera de las dos formas.
    if isinstance(valores_shap, list):
        valores_shap = valores_shap[1]

    shap_out.parent.mkdir(parents=True, exist_ok=True)
    plt.figure()
    shap.summary_plot(valores_shap, x_test, plot_type="bar", show=False)
    plt.tight_layout()
    plt.savefig(shap_out, dpi=150)
    plt.close()
    logger.info("Grafico SHAP guardado en %s", shap_out)

    importancia_shap = pd.DataFrame(
        {
            "feature": x_test.columns,
            "importancia_media_abs": abs(valores_shap).mean(axis=0),
        }
    ).sort_values("importancia_media_abs", ascending=False)

    return {
        "auc_roc": auc_roc,
        "matriz_confusion": matriz_confusion,
        "reporte_clasificacion": reporte_clasificacion,
        "importancia_shap": importancia_shap,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Entrena el modelo de riesgo crediticio"
    )
    parser.add_argument("--silver", default="data/silver")
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument("--labels", default="data/labels/risk_labels.parquet")
    parser.add_argument("--model-out", default="models/risk_model.joblib")
    parser.add_argument("--shap-out", default="models/shap_importancia.png")
    args = parser.parse_args()

    spark = get_spark_session("entrenar_modelo_riesgo")
    try:
        features = construir_features(spark, args.silver, args.gold)
        etiquetas = pd.read_parquet(args.labels)
        metricas = entrenar(
            features, etiquetas, Path(args.model_out), Path(args.shap_out)
        )
    except Exception:
        logger.exception("Fallo el entrenamiento del modelo")
        raise
    finally:
        spark.stop()

    print("\n=== Metricas del modelo de riesgo crediticio ===")
    print(f"AUC-ROC: {metricas['auc_roc']:.4f}")
    print("\nMatriz de confusion (filas=real, columnas=prediccion):")
    print(metricas["matriz_confusion"])
    print("\nPrecision/Recall por clase:")
    for clase, valores in metricas["reporte_clasificacion"].items():
        if clase in ("0", "1"):
            print(
                f"  Clase {clase}: precision={valores['precision']:.3f}, "
                f"recall={valores['recall']:.3f}, f1={valores['f1-score']:.3f}"
            )
    print("\nTop 5 features por importancia SHAP:")
    print(metricas["importancia_shap"].head(5).to_string(index=False))


if __name__ == "__main__":
    main()

"""
train_model.py

Entrena el modelo de clasificacion de riesgo crediticio, que calcula el
KPI probabilidad_impago del catalogo de Gold.

Lee las features de la tabla gold_features_cliente (construida por
build_features.py; la misma que lee predict_risk.py, para que
entrenamiento e inferencia nunca usen features distintas) y las
etiquetas de la tabla gold_etiquetas_impago (generate_labels.py: impago
como evento posterior, con un modelo latente). No necesita Spark: todo lo
que consume ya esta en Gold.

Ademas guarda la probabilidad de cada cliente calculada FUERA DE PLIEGUE
(validacion cruzada de 5 pliegues): la de cada cliente sale de un modelo
que no vio su etiqueta. predict_risk.py publica esa en Gold para los
clientes con etiqueta; predecirlos con el modelo final, que si los vio,
daba probabilidades sobreajustadas (decenas de clientes por encima de 90 %).

El AUC que imprime sale de UN solo split 80/20; con pocos positivos en
prueba oscila mucho (ver docs/technical-debt.md y experimentos/), asi que
no debe citarse sin su intervalo.

Uso:
    python train_model.py --gold data/gold/kpis.duckdb \
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
from sklearn.model_selection import (  # noqa: E402
    StratifiedKFold,
    cross_val_predict,
    train_test_split,
)
from xgboost import XGBClassifier  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.gold.etiquetas_gold import cargar_etiquetas  # noqa: E402
from src.gold.etiquetas_impago import verificar_sin_latentes  # noqa: E402
from src.gold.risk_features import (  # noqa: E402
    cargar_features,
    preparar_para_modelo,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def nuevo_modelo(y: pd.Series) -> XGBClassifier:
    """Mismos hiperparametros en el modelo final y en la validacion cruzada:
    si difirieran, la probabilidad fuera de pliegue no describiria al
    modelo que se publica."""
    positivos = int(y.sum())
    negativos = len(y) - positivos
    peso_positivo = negativos / positivos if positivos else 1.0
    return XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=peso_positivo,
        eval_metric="logloss",
        random_state=42,
    )


def probabilidad_fuera_de_pliegue(x: pd.DataFrame, y: pd.Series) -> pd.DataFrame:
    """Probabilidad de cada cliente predicha por un modelo entrenado sin el
    (5 pliegues estratificados, semilla fija)."""
    pliegues = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    probabilidades = cross_val_predict(
        nuevo_modelo(y), x, y, cv=pliegues, method="predict_proba"
    )[:, 1]
    return pd.DataFrame({"cliente_id": x.index, "probabilidad": probabilidades})


def entrenar(
    features: pd.DataFrame,
    etiquetas: pd.DataFrame,
    model_out: Path,
    shap_out: Path,
    oof_out: Path | None = None,
):
    """Entrena XGBoost, evalua contra el conjunto de prueba, genera
    el grafico SHAP, y guarda el modelo. Retorna las metricas para el
    reporte final."""
    dataset = features.join(
        etiquetas.set_index("cliente_id")["impago_posterior"].rename("label"),
        how="inner",
    )
    logger.info(
        "Dataset de entrenamiento: %d clientes con features y etiqueta", len(dataset)
    )

    x = dataset.drop(columns=["label"])
    y = dataset["label"]
    # Barrera contra la fuga: si z, choque o p llegaran aqui, el AUC dejaria
    # de medir nada.
    verificar_sin_latentes(x.columns, "la matriz de entrenamiento")

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

    modelo = nuevo_modelo(y_train)
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

    if oof_out is not None:
        oof = probabilidad_fuera_de_pliegue(x, y)
        oof_out.parent.mkdir(parents=True, exist_ok=True)
        oof.to_parquet(oof_out, index=False)
        logger.info(
            "Probabilidad fuera de pliegue guardada en %s: %d clientes, AUC %.3f",
            oof_out,
            len(oof),
            roc_auc_score(y, oof["probabilidad"]),
        )

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
        "n_test": len(y_test),
        "positivos_test": int(y_test.sum()),
        "matriz_confusion": matriz_confusion,
        "reporte_clasificacion": reporte_clasificacion,
        "importancia_shap": importancia_shap,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Entrena el modelo de riesgo crediticio"
    )
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument("--model-out", default="models/risk_model.joblib")
    parser.add_argument("--shap-out", default="models/shap_importancia.png")
    parser.add_argument(
        "--oof-out", default="models/probabilidad_fuera_de_pliegue.parquet"
    )
    args = parser.parse_args()

    try:
        features = preparar_para_modelo(cargar_features(args.gold))
        etiquetas = cargar_etiquetas(args.gold)
        metricas = entrenar(
            features,
            etiquetas,
            Path(args.model_out),
            Path(args.shap_out),
            Path(args.oof_out),
        )
    except Exception:
        logger.exception("Fallo el entrenamiento del modelo")
        raise

    print("\n=== Metricas del modelo de riesgo crediticio ===")
    print(f"AUC-ROC: {metricas['auc_roc']:.4f}")
    print(
        f"  (un solo split: {metricas['n_test']} clientes de prueba, "
        f"{metricas['positivos_test']} positivos; con tan pocos positivos oscila "
        "mucho entre corridas, no citarlo sin intervalo)"
    )
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

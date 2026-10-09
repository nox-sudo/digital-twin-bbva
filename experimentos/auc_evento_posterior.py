"""
experimentos/auc_evento_posterior.py

Evalua el modelo de riesgo contra la etiqueta de impago como evento posterior
(generate_labels.py + src/gold/etiquetas_impago.py). Reemplaza a
auc_circularidad.py, cuyo paso E4 reconstruia el XOR de ruido que ya no existe.
Ese experimento original sigue en el historial: el diseno que midio (regla mas
XOR) esta en el tag v0.3.0 y el script en el commit 3423e36; ver
docs/technical-debt.md.

Que mide (los parametros de la etiqueta se fijaron antes de ver resultados, en
config/etiquetas_impago.yaml):

  E1  determinismo: 3 entrenamientos con la misma semilla y el mismo split;
      reporta los 3 AUC.
  E2  validacion cruzada estratificada (5 pliegues x 5 repeticiones) con el
      mismo XGBoost de train_model.py: AUC por pliegue (media y desviacion) y
      AUC fuera de pliegue por repeticion (minimo y maximo).
  E3  oraculo: AUC de la probabilidad verdadera p (de la auditoria) contra la
      etiqueta. Es el techo de cualquier modelo que no vea z ni choque.
  E4  signos y orden: regresion logistica sobre las 3 variables observadas
      estandarizadas, comparada con los coeficientes verdaderos de la
      configuracion. Los estimados salen atenuados respecto a los verdaderos
      (variable omitida en logistica), asi que se comparan signos y orden, no
      magnitudes.
  E5  ablacion: el mismo CV sin las 3 variables observadas. Es una metrica de
      cuanta senal queda en las demas, no una prueba.

Solo lee Gold y la auditoria; no escribe modelos ni capas. Requiere XGBoost: en
macOS sin libomp (brew install libomp) no corre. Uso, tras correr el pipeline:

    uv run python experimentos/auc_evento_posterior.py \
        --gold data/gold/kpis.duckdb \
        --auditoria data/auditoria/etiquetas_latentes.parquet

Para evaluar otro tamano (por ejemplo 5,000 clientes) se corre el pipeline con
otra sesion y otro DATA_ROOT, y se apunta --gold y --auditoria ahi.
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold, train_test_split
from xgboost import XGBClassifier

sys.path.insert(0, os.getcwd())

from src.gold.etiquetas_gold import cargar_etiquetas  # noqa: E402
from src.gold.etiquetas_impago import (  # noqa: E402
    VARIABLES_OBSERVADAS,
    cargar_config,
    coeficientes_verdaderos,
    comparar_signos_y_orden,
    estandarizar_variables,
    factor_atenuacion,
    verificar_sin_latentes,
)
from src.gold.risk_features import cargar_features, preparar_para_modelo  # noqa: E402

SEMILLA = 42


def nuevo_modelo(y_entrenamiento: pd.Series) -> XGBClassifier:
    """Mismo modelo y mismos hiperparametros que train_model.py."""
    positivos = int(y_entrenamiento.sum())
    negativos = len(y_entrenamiento) - positivos
    return XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=negativos / positivos if positivos else 1.0,
        eval_metric="logloss",
        random_state=SEMILLA,
    )


def validacion_cruzada(x: pd.DataFrame, y: pd.Series, pliegues: int, repeticiones: int):
    """AUC por pliegue y AUC fuera de pliegue por repeticion."""
    esquema = RepeatedStratifiedKFold(
        n_splits=pliegues, n_repeats=repeticiones, random_state=SEMILLA
    )
    por_pliegue = []
    fuera = {r: np.zeros(len(x)) for r in range(repeticiones)}
    for i, (entrenamiento, prueba) in enumerate(esquema.split(x, y)):
        modelo = nuevo_modelo(y.iloc[entrenamiento]).fit(
            x.iloc[entrenamiento], y.iloc[entrenamiento]
        )
        p = modelo.predict_proba(x.iloc[prueba])[:, 1]
        por_pliegue.append(roc_auc_score(y.iloc[prueba], p))
        fuera[i // pliegues][prueba] = p
    por_repeticion = [roc_auc_score(y, fuera[r]) for r in range(repeticiones)]
    return np.array(por_pliegue), np.array(por_repeticion)


def informar_cv(
    nombre: str, x: pd.DataFrame, y: pd.Series, pliegues: int, repeticiones: int
):
    por_pliegue, por_repeticion = validacion_cruzada(x, y, pliegues, repeticiones)
    print(
        f"RESULT {nombre}: AUC por pliegue {por_pliegue.mean():.3f} +- {por_pliegue.std():.3f} "
        f"(min {por_pliegue.min():.3f}, max {por_pliegue.max():.3f}) | AUC fuera de pliegue "
        f"por repeticion media {por_repeticion.mean():.3f} "
        f"(min {por_repeticion.min():.3f}, max {por_repeticion.max():.3f})"
    )
    return por_repeticion.mean()


def main():
    parser = argparse.ArgumentParser(
        description="Evalua el modelo contra el evento posterior"
    )
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument(
        "--auditoria", default="data/auditoria/etiquetas_latentes.parquet"
    )
    parser.add_argument("--config", default="config/etiquetas_impago.yaml")
    parser.add_argument("--pliegues", type=int, default=5)
    parser.add_argument("--repeticiones", type=int, default=5)
    args = parser.parse_args()

    cfg = cargar_config(args.config)
    features_legibles = cargar_features(args.gold)
    etiquetas = cargar_etiquetas(args.gold).set_index("cliente_id")
    auditoria = pd.read_parquet(args.auditoria).set_index("cliente_id")

    x = preparar_para_modelo(features_legibles)
    verificar_sin_latentes(x.columns, "la matriz de evaluacion")
    y = etiquetas["impago_posterior"].reindex(x.index).astype(int)
    p_verdadera = auditoria["p"].reindex(x.index)
    if y.isna().any() or p_verdadera.isna().any():
        raise RuntimeError("faltan etiquetas o auditoria para algunos clientes")

    n, positivos = len(y), int(y.sum())
    fecha = etiquetas["fecha_observacion"].iloc[0]
    print(
        f"RESULT configuracion: {n} clientes, {positivos} positivos ({positivos / n:.3f}), "
        f"tasa objetivo {cfg.tasa_objetivo}, semilla {cfg.semilla}, horizonte "
        f"{cfg.horizonte_meses} meses, fecha de observacion {fecha}"
    )

    # E1. Determinismo: 3 entrenamientos, misma semilla, mismo split
    aucs = []
    for _ in range(3):
        xe, xp, ye, yp = train_test_split(
            x, y, test_size=0.2, stratify=y, random_state=SEMILLA
        )
        modelo = nuevo_modelo(ye).fit(xe, ye)
        aucs.append(roc_auc_score(yp, modelo.predict_proba(xp)[:, 1]))
    print(
        "RESULT E1 determinismo: AUC de los 3 entrenamientos = "
        + ", ".join(f"{a:.6f}" for a in aucs)
        + f" | iguales: {len(set(aucs)) == 1}"
    )

    # E2. Validacion cruzada con todas las features
    auc_modelo = informar_cv("E2 CV completo", x, y, args.pliegues, args.repeticiones)

    # E3. Oraculo
    auc_oraculo = roc_auc_score(y, p_verdadera)
    print(
        f"RESULT E3 oraculo: AUC de la probabilidad verdadera = {auc_oraculo:.3f}; "
        f"hueco contra el modelo (CV) = {auc_oraculo - auc_modelo:.3f}; "
        f"modelo por debajo del oraculo: {auc_modelo < auc_oraculo}"
    )

    # E4. Signos y orden, no magnitudes
    estandarizadas = estandarizar_variables(
        features_legibles, cfg.winsor_inferior, cfg.winsor_superior
    ).reindex(x.index)
    logistica = LogisticRegression(C=1e6, max_iter=2000).fit(estandarizadas, y)
    estimados = dict(zip(VARIABLES_OBSERVADAS, logistica.coef_[0]))
    verdaderos = coeficientes_verdaderos(cfg)
    comparacion = comparar_signos_y_orden(estimados, verdaderos)
    print(
        f"RESULT E4 atenuacion teorica (omitir z y choque): estimado/verdadero ~ "
        f"{factor_atenuacion(cfg):.3f}"
    )
    for variable in VARIABLES_OBSERVADAS:
        print(
            f"RESULT E4 {variable}: verdadero {verdaderos[variable]:+.2f}, estimado "
            f"{estimados[variable]:+.2f} (estimado/verdadero "
            f"{estimados[variable] / verdaderos[variable]:.2f})"
        )
    print(
        f"RESULT E4 signos coinciden: {comparacion.signos_coinciden} | orden "
        f"{comparacion.orden_verdadero} recuperado: {comparacion.orden_coincide} "
        f"(estimado {comparacion.orden_estimado}); es una metrica, no una prueba"
    )

    # E5. Ablacion: sin las 3 variables observadas
    columnas_ablacion = [c for c in x.columns if c not in VARIABLES_OBSERVADAS]
    informar_cv(
        "E5 ablacion sin las 3 variables",
        x[columnas_ablacion],
        y,
        args.pliegues,
        args.repeticiones,
    )


if __name__ == "__main__":
    main()

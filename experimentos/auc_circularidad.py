"""
experimentos/auc_circularidad.py

Mide cuanto del AUC del modelo de riesgo es poder predictivo y cuanto es la
circularidad de las etiquetas sinteticas (generate_labels.py las deriva de 3
variables del mismo feature store). Las cifras de docs/technical-debt.md salen de
este script.

Que mide:
  E1  determinismo: mismo split y semilla que train_model.py, dos veces.
  E2  AUC con validacion cruzada estratificada 5 pliegues x 10 repeticiones.
  E3  ablacion: sin las 3 variables de la etiqueta, y solo con ellas.
  E4  techo del AUC en estos datos (regla exacta contra etiqueta con ruido) y que
      tanto reconstruye el modelo la regla sin ruido.

Solo lee Gold y las etiquetas; no escribe modelos ni capas. Requiere XGBoost, asi
que en macOS sin libomp se corre en el contenedor:
    docker compose run --rm worker experimentos/auc_circularidad.py
(el worker monta data/ y config/; el archivo entra a la imagen al reconstruirla
con demo.sh).
"""

import os
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold, train_test_split
from xgboost import XGBClassifier

sys.path.insert(0, os.getcwd())
from src.gold.risk_features import cargar_features, preparar_para_modelo  # noqa: E402

VARS_ETIQUETA = ["ratio_endeudamiento", "capacidad_ahorro", "uso_linea_credito"]

features = preparar_para_modelo(cargar_features("data/gold/kpis.duckdb"))
etiquetas = pd.read_parquet("data/labels/risk_labels.parquet")
ds = features.join(etiquetas.set_index("cliente_id")["label"], how="inner")
X, y = ds.drop(columns=["label"]), ds["label"].astype(int)
print(
    f"RESULT dataset: {len(ds)} clientes, {int(y.sum())} positivos, {X.shape[1]} columnas"
)
presentes = [v for v in VARS_ETIQUETA if v in X.columns]
faltan = [v for v in VARS_ETIQUETA if v not in X.columns]
print(f"RESULT variables de la etiqueta en X: {presentes} faltan={faltan}")


def modelo(y_train):
    pos = int(y_train.sum())
    return XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=(len(y_train) - pos) / pos if pos else 1.0,
        eval_metric="logloss",
        random_state=42,
    )


# E1. Determinismo: mismo split y semilla que train_model.py, dos veces
aucs = []
for _ in range(2):
    xt, xs, yt, ys = train_test_split(X, y, test_size=0.2, stratify=y, random_state=42)
    m = modelo(yt).fit(xt, yt)
    aucs.append(roc_auc_score(ys, m.predict_proba(xs)[:, 1]))
print(
    f"RESULT E1 determinismo: AUC corrida1={aucs[0]:.6f} corrida2={aucs[1]:.6f} "
    f"iguales={aucs[0] == aucs[1]}"
)


def cv(Xc, repeticiones=10):
    rskf = RepeatedStratifiedKFold(n_splits=5, n_repeats=repeticiones, random_state=42)
    por_fold, oof = [], {r: np.zeros(len(Xc)) for r in range(repeticiones)}
    for i, (tr, te) in enumerate(rskf.split(Xc, y)):
        m = modelo(y.iloc[tr]).fit(Xc.iloc[tr], y.iloc[tr])
        p = m.predict_proba(Xc.iloc[te])[:, 1]
        por_fold.append(roc_auc_score(y.iloc[te], p))
        oof[i // 5][te] = p
    por_rep = [roc_auc_score(y, oof[r]) for r in range(repeticiones)]
    return np.array(por_fold), np.array(por_rep), oof


def informe(nombre, Xc):
    f, r, oof = cv(Xc)
    print(
        f"RESULT {nombre}: AUC por fold media={f.mean():.3f} sd={f.std():.3f} "
        f"[min {f.min():.3f}, max {f.max():.3f}] | AUC out-of-fold por repeticion "
        f"media={r.mean():.3f} [p2.5 {np.percentile(r, 2.5):.3f}, "
        f"p97.5 {np.percentile(r, 97.5):.3f}]"
    )
    return oof


oof_full = informe("E2 CV completo (todas las features)", X)
informe(
    "E3 ablacion: SIN las 3 variables de la etiqueta",
    X.drop(columns=[v for v in VARS_ETIQUETA if v in X.columns]),
)
informe(
    "E3b SOLO las 3 variables de la etiqueta",
    X[[v for v in VARS_ETIQUETA if v in X.columns]],
)

# E4. Techo real en ESTOS datos: reconstruir la regla sin ruido
# (etiqueta XOR ruido, con la semilla 42 y p=0.08 de generate_labels.py).
etq = etiquetas.reset_index(drop=True)
ruido = np.random.default_rng(42).random(len(etq)) < 0.08
print(
    f"RESULT E4 ruido reconstruido: {int(ruido.sum())} etiquetas invertidas "
    "(debe coincidir con el log de generate_labels.py)"
)
regla = pd.Series(
    (etq["label"].astype(bool) ^ ruido).astype(int).values,
    index=etq["cliente_id"].values,
)
regla = regla.reindex(ds.index)
ruidoso = ds["label"].astype(int)
print(
    f"RESULT E4 regla sin ruido: {int(regla.sum())} positivos; "
    f"etiqueta con ruido: {int(ruidoso.sum())}"
)
techo = roc_auc_score(ruidoso, regla)
print(
    f"RESULT E4 techo (score = regla exacta): AUC(etiqueta con ruido, regla) = {techo:.3f}"
)
auc_regla = [roc_auc_score(regla, oof_full[r]) for r in oof_full]
print(
    "RESULT E4 el modelo reconstruye la regla: "
    f"AUC(regla sin ruido, prediccion fuera de pliegue) media={np.mean(auc_regla):.3f}"
)

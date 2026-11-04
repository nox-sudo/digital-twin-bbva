"""
experimentos/calibracion_riesgo.py

Compara la calibracion de la probabilidad de impago publicada con y sin
scale_pos_weight (deuda "Probabilidad de impago descalibrada por
scale_pos_weight" en docs/technical-debt.md). El criterio de decision se
fijo en ese documento antes de correr este script.

La probabilidad que publica predict_risk.py es la fuera de pliegue de
train_model.py (5 pliegues estratificados, semilla 42), asi que aqui se mide
esa misma probabilidad: cada cliente la recibe de un modelo que no lo vio.

Variantes:

  A  modelo actual: XGBoost de train_model.py con scale_pos_weight
     (negativos / positivos).
  B  el mismo XGBoost sin scale_pos_weight.
  C  (--con-c) A mas calibracion logistica (Platt) anidada: en cada pliegue
     externo, la logistica se ajusta sobre probabilidades fuera de pliegue
     del conjunto de entrenamiento (validacion cruzada interna) y se aplica
     al pliegue de prueba. Asi la calibracion tampoco ve al cliente que
     califica. Solo se corre si B no cumple el criterio.

Para cada variante:

  - AUC, Brier y log loss de la probabilidad fuera de pliegue, en 5
    repeticiones de 5 pliegues (media, minimo y maximo);
  - media predicha contra tasa real;
  - clientes por arriba de 0.90;
  - curva de calibracion por deciles de la probabilidad predicha y clientes
    por banda de config/dashboard.yaml, ambas en la repeticion con los
    pliegues de train_model.py (la que se publica).

Solo lee Gold; no escribe modelos ni capas. Uso, tras correr el pipeline:

    uv run python experimentos/calibracion_riesgo.py --gold data/gold/kpis.duckdb

Para 5,000 clientes se corre el pipeline con otra sesion y otro DATA_ROOT, y
se apunta --gold ahi (como en experimentos/auc_evento_posterior.py).
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from xgboost import XGBClassifier

sys.path.insert(0, os.getcwd())

from src.gold.etiquetas_gold import cargar_etiquetas  # noqa: E402
from src.gold.etiquetas_impago import verificar_sin_latentes  # noqa: E402
from src.gold.risk_features import cargar_features, preparar_para_modelo  # noqa: E402

SEMILLA = 42
PLIEGUES = 5
EPSILON = 1e-6


def nuevo_modelo(y: pd.Series, con_peso: bool) -> XGBClassifier:
    """Hiperparametros de train_model.py; con_peso decide scale_pos_weight."""
    positivos = int(y.sum())
    negativos = len(y) - positivos
    peso = negativos / positivos if (con_peso and positivos) else 1.0
    return XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=peso,
        eval_metric="logloss",
        random_state=SEMILLA,
    )


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPSILON, 1 - EPSILON)
    return np.log(p / (1 - p)).reshape(-1, 1)


def fuera_de_pliegue(
    x: pd.DataFrame, y: pd.Series, semilla: int, con_peso: bool, peso_de: pd.Series
) -> np.ndarray:
    """Probabilidad fuera de pliegue con los pliegues de esa semilla.

    peso_de es la serie con la que se calcula scale_pos_weight. train_model.py
    lo calcula con todas las etiquetas (no las del pliegue), y aqui se hace
    igual para medir exactamente lo que se publica.
    """
    pliegues = StratifiedKFold(n_splits=PLIEGUES, shuffle=True, random_state=semilla)
    p = np.zeros(len(x))
    for entrena, prueba in pliegues.split(x, y):
        modelo = nuevo_modelo(peso_de, con_peso)
        modelo.fit(x.iloc[entrena], y.iloc[entrena])
        p[prueba] = modelo.predict_proba(x.iloc[prueba])[:, 1]
    return p


def fuera_de_pliegue_platt(x: pd.DataFrame, y: pd.Series, semilla: int) -> np.ndarray:
    """Variante C: A calibrada con Platt anidado."""
    pliegues = StratifiedKFold(n_splits=PLIEGUES, shuffle=True, random_state=semilla)
    p = np.zeros(len(x))
    for entrena, prueba in pliegues.split(x, y):
        x_e, y_e = x.iloc[entrena], y.iloc[entrena]
        # Probabilidades fuera de pliegue dentro del entrenamiento, con el
        # mismo peso que el modelo final de este pliegue.
        interna = fuera_de_pliegue(x_e, y_e, semilla + 1000, True, y)
        platt = LogisticRegression(C=1e6).fit(logit(interna), y_e)
        modelo = nuevo_modelo(y, True).fit(x_e, y_e)
        crudo = modelo.predict_proba(x.iloc[prueba])[:, 1]
        p[prueba] = platt.predict_proba(logit(crudo))[:, 1]
    return p


def metricas(y: pd.Series, p: np.ndarray) -> dict:
    return {
        "auc": roc_auc_score(y, p),
        "brier": brier_score_loss(y, p),
        "log_loss": log_loss(y, np.clip(p, EPSILON, 1 - EPSILON)),
        "media": float(p.mean()),
        "sobre_090": int((p > 0.90).sum()),
    }


def deciles(y: pd.Series, p: np.ndarray) -> pd.DataFrame:
    tabla = pd.DataFrame({"p": p, "y": y.values})
    tabla["decil"] = pd.qcut(tabla["p"].rank(method="first"), 10, labels=False) + 1
    return tabla.groupby("decil").agg(
        clientes=("y", "size"),
        predicha=("p", "mean"),
        observada=("y", "mean"),
    )


def por_banda(p: np.ndarray, bandas: list[dict]) -> dict:
    limites = [b["desde"] for b in bandas]
    nombres = [b["nombre"] for b in bandas]
    indice = np.searchsorted(limites, p, side="right") - 1
    return {nombre: int((indice == i).sum()) for i, nombre in enumerate(nombres)}


def main():
    parser = argparse.ArgumentParser(description="Calibracion de la probabilidad")
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument("--dashboard", default="config/dashboard.yaml")
    parser.add_argument("--repeticiones", type=int, default=5)
    parser.add_argument(
        "--con-c", action="store_true", help="Corre tambien la variante C (Platt)"
    )
    args = parser.parse_args()

    x = preparar_para_modelo(cargar_features(args.gold))
    etiquetas = cargar_etiquetas(args.gold).set_index("cliente_id")["impago_posterior"]
    dataset = x.join(etiquetas.rename("label"), how="inner")
    x, y = dataset.drop(columns=["label"]), dataset["label"]
    verificar_sin_latentes(x.columns, "la matriz del experimento")
    with open(args.dashboard, encoding="utf-8") as f:
        bandas = yaml.safe_load(f)["bandas_riesgo"]

    print(
        f"RESULT datos: {len(y)} clientes, {int(y.sum())} positivos, "
        f"tasa real {y.mean():.4f}"
    )

    variantes = {
        "A": lambda s: fuera_de_pliegue(x, y, s, True, y),
        "B": lambda s: fuera_de_pliegue(x, y, s, False, y),
    }
    if args.con_c:
        variantes["C"] = lambda s: fuera_de_pliegue_platt(x, y, s)

    for nombre, calcular in variantes.items():
        # Repeticion 0: la semilla de train_model.py, la que se publica.
        por_rep = []
        publicada = None
        for r in range(args.repeticiones):
            p = calcular(SEMILLA + r)
            por_rep.append(metricas(y, p))
            if r == 0:
                publicada = p
        tabla = pd.DataFrame(por_rep)
        resumen = " | ".join(
            f"{col} {tabla[col].mean():.4f} ({tabla[col].min():.4f} a {tabla[col].max():.4f})"
            for col in ["auc", "brier", "log_loss", "media"]
        )
        print(f"RESULT {nombre}: {resumen} | sobre_090 {tabla['sobre_090'].mean():.1f}")
        m0 = metricas(y, publicada)
        print(
            f"RESULT {nombre} publicada: auc {m0['auc']:.4f} brier {m0['brier']:.4f} "
            f"log_loss {m0['log_loss']:.4f} media {m0['media']:.4f} "
            f"sobre_090 {m0['sobre_090']} bandas {por_banda(publicada, bandas)}"
        )
        print(f"DECILES {nombre}")
        print(deciles(y, publicada).round(4).to_string())


if __name__ == "__main__":
    main()

"""
src/gold/calibracion.py

Calibracion logistica (Platt) del modelo de riesgo. XGBoost se entrena con
scale_pos_weight, que mejora la sensibilidad pero infla la probabilidad: con
la sesion base de 500 clientes la media predicha era 0.205 contra una tasa
real de 0.162. La calibracion es una recta sobre el margen (log-odds) del
modelo, p = sigmoide(pendiente * margen + intercepto), ajustada con
probabilidades fuera de pliegue para que no vea a los clientes que califica.
Decision y numeros en docs/technical-debt.md.

ModeloCalibrado envuelve al XGBClassifier y expone lo que usan los demas
modulos: predict_proba ya calibrado (predict_risk.py, monitor_drift.py),
feature_names_in_ y get_booster(). Como la recta es sobre el margen, la
explicacion SHAP sigue siendo exacta: cada contribucion se multiplica por la
pendiente y el valor base pasa a pendiente * base + intercepto
(src/gold/explicacion.py).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

# Sin regularizacion efectiva: solo se ajustan dos parametros.
C_PLATT = 1e6


def sigmoide(x):
    return 1.0 / (1.0 + np.exp(-x))


def ajustar_platt(margen, y) -> tuple[float, float]:
    """Pendiente e intercepto de la logistica de y sobre el margen."""
    margen = np.asarray(margen, dtype=float).reshape(-1, 1)
    logistica = LogisticRegression(C=C_PLATT).fit(margen, np.asarray(y))
    return float(logistica.coef_[0][0]), float(logistica.intercept_[0])


@dataclass
class ModeloCalibrado:
    modelo: object  # XGBClassifier entrenado
    pendiente: float
    intercepto: float

    @property
    def feature_names_in_(self):
        return self.modelo.feature_names_in_

    def get_booster(self):
        return self.modelo.get_booster()

    def margen(self, x: pd.DataFrame) -> np.ndarray:
        return self.modelo.predict(x, output_margin=True)

    def predict_proba(self, x: pd.DataFrame) -> np.ndarray:
        p = sigmoide(self.pendiente * self.margen(x) + self.intercepto)
        return np.column_stack([1 - p, p])

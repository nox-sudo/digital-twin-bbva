"""
tests/test_model.py

Pruebas del modelo de riesgo crediticio. No dependen de que el
pipeline completo (Silver -> Gold -> train_model.py) se haya corrido
antes: entrenan un modelo minimo en memoria con datos sinteticos,
igual que test_validation.py prueba el motor de Silver sin necesitar
un Bronze real.
"""

import joblib
import numpy as np
import pandas as pd
from xgboost import XGBClassifier


def _modelo_de_prueba() -> XGBClassifier:
    """Modelo minimo entrenado sobre datos sinteticos en memoria,
    para probar el contrato de carga/prediccion; la calidad del
    modelo real la reporta train_model.py."""
    rng = np.random.default_rng(42)
    x = pd.DataFrame(rng.normal(size=(50, 4)), columns=["a", "b", "c", "d"])
    y = (x["a"] + x["b"] > 0).astype(int)

    modelo = XGBClassifier(n_estimators=10, max_depth=2, random_state=42)
    modelo.fit(x, y)
    return modelo


def test_modelo_carga_sin_errores(tmp_path):
    """joblib.dump seguido de joblib.load debe devolver un modelo
    utilizable, con el mismo contrato que usa predict_risk.py."""
    modelo_original = _modelo_de_prueba()
    ruta = tmp_path / "modelo_prueba.joblib"
    joblib.dump(modelo_original, ruta)

    modelo_cargado = joblib.load(ruta)

    assert modelo_cargado is not None
    assert hasattr(modelo_cargado, "predict_proba")


def test_predicciones_son_scores_validos(tmp_path):
    """predict_proba debe devolver probabilidades entre 0.0 y 1.0,
    el mismo contrato que predict_risk.py asume al escribir
    valor_numerico de vuelta a Gold."""
    modelo = _modelo_de_prueba()
    ruta = tmp_path / "modelo_prueba.joblib"
    joblib.dump(modelo, ruta)
    modelo_cargado = joblib.load(ruta)

    rng = np.random.default_rng(7)
    entrada_nueva = pd.DataFrame(rng.normal(size=(10, 4)), columns=["a", "b", "c", "d"])
    probabilidades = modelo_cargado.predict_proba(entrada_nueva)[:, 1]

    assert probabilidades.min() >= 0.0
    assert probabilidades.max() <= 1.0

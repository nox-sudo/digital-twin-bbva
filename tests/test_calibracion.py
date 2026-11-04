"""
tests/test_calibracion.py

Calibracion logistica del modelo de riesgo (src/gold/calibracion.py) y su
efecto en la explicacion SHAP (src/gold/explicacion.py).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from xgboost import XGBClassifier

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gold import explicacion as ex  # noqa: E402
from src.gold.calibracion import ModeloCalibrado, ajustar_platt, sigmoide  # noqa: E402


@pytest.fixture
def modelo_y_datos():
    rng = np.random.default_rng(0)
    x = pd.DataFrame(rng.normal(size=(300, 3)), columns=["a", "b", "c"])
    y = pd.Series((rng.random(300) < sigmoide(1.5 * x["a"] - x["b"])).astype(int))
    modelo = XGBClassifier(n_estimators=20, max_depth=2, random_state=0)
    return modelo.fit(x, y), x, y


def test_platt_recupera_la_recta():
    rng = np.random.default_rng(1)
    margen = rng.normal(scale=2, size=20000)
    y = (rng.random(20000) < sigmoide(0.6 * margen - 1.2)).astype(int)
    pendiente, intercepto = ajustar_platt(margen, y)
    assert pendiente == pytest.approx(0.6, abs=0.05)
    assert intercepto == pytest.approx(-1.2, abs=0.05)


def test_sin_calibrar_es_el_modelo_original(modelo_y_datos):
    modelo, x, _ = modelo_y_datos
    identidad = ModeloCalibrado(modelo, 1.0, 0.0)
    assert np.allclose(identidad.predict_proba(x), modelo.predict_proba(x), atol=1e-6)


def test_predict_proba_aplica_la_recta_al_margen(modelo_y_datos):
    modelo, x, _ = modelo_y_datos
    calibrado = ModeloCalibrado(modelo, 0.5, -0.8)
    margen = modelo.predict(x, output_margin=True)
    p = calibrado.predict_proba(x)
    assert np.allclose(p[:, 1], sigmoide(0.5 * margen - 0.8))
    assert np.allclose(p.sum(axis=1), 1.0)
    assert list(calibrado.feature_names_in_) == ["a", "b", "c"]


def test_la_calibracion_no_cambia_el_orden(modelo_y_datos):
    modelo, x, _ = modelo_y_datos
    crudo = modelo.predict_proba(x)[:, 1]
    calibrado = ModeloCalibrado(modelo, 0.4, -1.0).predict_proba(x)[:, 1]
    assert (
        np.argsort(crudo, kind="stable") == np.argsort(calibrado, kind="stable")
    ).all()


def test_contribuciones_suman_el_margen_calibrado(modelo_y_datos):
    modelo, x, _ = modelo_y_datos
    calibrado = ModeloCalibrado(modelo, 0.5, -0.8)
    aportes = ex.contribuciones(calibrado, x)
    assert np.allclose(sigmoide(aportes.sum(axis=1)), calibrado.predict_proba(x)[:, 1])
    # Mismo signo y orden de los factores que sin calibrar.
    crudos = ex.contribuciones(modelo, x).drop(columns="base")
    assert (np.sign(aportes.drop(columns="base")) == np.sign(crudos)).all().all()

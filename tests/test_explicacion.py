"""
tests/test_explicacion.py

Explicacion SHAP del score de un cliente (src/gold/explicacion.py):

  - La suma de las contribuciones mas la base reproduce la probabilidad del
    modelo, cliente por cliente.
  - El score publicado sale del modelo que lo produjo: el del pliegue para
    los clientes con probabilidad fuera de pliegue (train_model.py), el
    final para los demas.
  - Cada variable del modelo tiene su frase.
"""

import math
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datos_vista_cliente import (  # noqa: E402
    NUMERICAS,
    entrenar,
    etiquetas_sinteticas,
    features_sinteticas,
)
from src.gold import explicacion as ex  # noqa: E402
from src.gold.risk_features import KPI_CATEGORICO, preparar_para_modelo  # noqa: E402


@pytest.fixture(scope="module")
def datos():
    features = features_sinteticas(150)
    y = etiquetas_sinteticas(features)
    return features, y, entrenar(features, y)


def logit(p: float) -> float:
    return math.log(p / (1 - p))


def test_la_explicacion_reproduce_la_probabilidad(datos):
    features, _, modelo = datos
    matriz = preparar_para_modelo(features, list(modelo.feature_names_in_))
    esperadas = modelo.predict_proba(matriz)[:, 1]
    for cliente, esperada in zip(features.index[:40], esperadas[:40]):
        e = ex.explicar(modelo, features, cliente)
        assert e.probabilidad == pytest.approx(esperada, abs=1e-5)
        suma = logit(e.probabilidad_base) + sum(f.contribucion for f in e.factores)
        assert suma == pytest.approx(logit(esperada), abs=1e-4)


def test_un_factor_por_variable_y_la_categoria_agrupada(datos):
    features, _, modelo = datos
    e = ex.explicar(modelo, features, features.index[0])
    variables = [f.variable for f in e.factores]
    assert sorted(variables) == sorted(NUMERICAS + [KPI_CATEGORICO])
    assert not any(v.startswith("categoria_dominante_") for v in variables)
    # De mayor a menor efecto.
    efectos = [abs(f.contribucion) for f in e.factores]
    assert efectos == sorted(efectos, reverse=True)


def test_config_redacta_todas_las_variables():
    cfg = ex.cargar_config()
    assert set(cfg["variables"]) == set(NUMERICAS + [KPI_CATEGORICO])
    assert cfg["factores_mostrados"] >= 1


def test_variable_sin_frase_es_error(datos):
    features, _, modelo = datos
    cfg = ex.cargar_config()
    incompleta = {**cfg, "variables": dict(cfg["variables"])}
    del incompleta["variables"]["edad_anios"]
    with pytest.raises(ValueError, match="edad_anios"):
        ex.explicar(modelo, features, features.index[0], incompleta)


@pytest.mark.parametrize(
    "variable, valor, esperado",
    [
        ("capacidad_ahorro", -4091.4, "te quedan -$4,091 al mes"),
        ("uso_linea_credito", 0.42, "Usas el 42 % del límite"),
        ("ratio_endeudamiento", 3.44, "3.4 veces"),
        ("antiguedad_dias", 1278, "3.5 años"),
        ("antiguedad_dias", 120, "4 meses"),
        ("tiene_tarjeta_credito", 1, "Tienes tarjeta de crédito"),
        ("tiene_tarjeta_credito", 0, "No tienes tarjeta de crédito"),
        ("categoria_gasto_dominante", "educacion", "es educación"),
        ("saldo_liquido_disponible", float("nan"), "sin dato"),
    ],
)
def test_frases(variable, valor, esperado):
    assert esperado in ex.frase(variable, valor, ex.cargar_config())


# --- El modelo que produjo el score ------------------------------------------


def test_fuera_de_pliegue_igual_a_cross_val_predict(datos):
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    import train_model

    features, y, _ = datos
    x = preparar_para_modelo(features)
    oof, modelos = train_model.probabilidad_fuera_de_pliegue(x, y)
    referencia = cross_val_predict(
        train_model.nuevo_modelo(y),
        x,
        y,
        cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=42),
        method="predict_proba",
    )[:, 1]
    assert np.allclose(oof["probabilidad"], referencia, atol=1e-6)
    assert len(modelos) == 5 and set(oof["pliegue"]) == set(range(5))


def test_el_score_fuera_de_pliegue_se_explica_con_su_modelo(datos):
    import train_model

    features, y, final = datos
    oof, modelos = train_model.probabilidad_fuera_de_pliegue(
        preparar_para_modelo(features), y
    )
    oof = oof.reset_index(drop=True)
    for fila in oof.head(30).itertuples():
        modelo = ex.modelo_del_score(fila.cliente_id, final, oof, modelos)
        assert modelo is modelos[fila.pliegue]
        e = ex.explicar(modelo, features, fila.cliente_id)
        assert e.probabilidad == pytest.approx(fila.probabilidad, abs=1e-5)


def test_reglas_para_elegir_el_modelo(datos):
    features, _, final = datos
    oof = pd.DataFrame({"cliente_id": ["A"], "probabilidad": [0.2], "pliegue": [1]})
    pliegues = ["m0", "m1"]
    assert ex.modelo_del_score("A", final, None, None) is final
    assert ex.modelo_del_score("B", final, oof, pliegues) is final
    assert ex.modelo_del_score("A", final, oof, pliegues) == "m1"
    with pytest.raises(ex.SinExplicacion):
        ex.modelo_del_score("A", final, oof, None)
    with pytest.raises(ex.SinExplicacion):
        ex.modelo_del_score("A", final, oof.drop(columns="pliegue"), pliegues)


def test_explicar_cliente_sin_modelo(tmp_path):
    with pytest.raises(ex.SinExplicacion):
        ex.explicar_cliente(
            "no-importa.duckdb",
            str(tmp_path / "no-existe.joblib"),
            str(tmp_path / "oof.parquet"),
            str(tmp_path / "p.joblib"),
            "CLI-1",
        )


def test_explicar_cliente_lee_los_artefactos(tmp_path, datos):
    from datos_vista_cliente import crear_gold_vista

    cfg = crear_gold_vista(tmp_path)
    modelo = joblib.load(cfg["modelo"])
    e = ex.explicar_cliente(
        cfg["gold_duckdb"],
        cfg["modelo"],
        cfg["probabilidad_oof"],
        cfg["modelos_pliegues"],
        "CLI-20K",
    )
    from src.gold.risk_features import cargar_features

    f = cargar_features(cfg["gold_duckdb"])
    esperada = modelo.predict_proba(
        preparar_para_modelo(f.loc[["CLI-20K"]], list(modelo.feature_names_in_))
    )[0, 1]
    assert e.probabilidad == pytest.approx(esperada, abs=1e-5)

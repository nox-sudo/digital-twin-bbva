"""
tests/test_etiquetas_impago.py

Pruebas del modelo latente de la etiqueta de impago
(src/gold/etiquetas_impago.py). Corren en memoria, sin Spark ni DuckDB, sobre un
feature store sintetico de 5,000 clientes con la forma del real: ratio de
endeudamiento y uso de linea con mucha masa en 0 (clientes sin producto) y
colas largas, capacidad de ahorro con colas a ambos lados.

La comparacion con el oraculo usa el modelo de scikit-learn y no XGBoost a
proposito: lo que se prueba (un modelo que solo ve las variables observadas no
puede superar a la probabilidad verdadera) no depende del algoritmo, y asi la
prueba corre tambien en macOS sin libomp.
"""

import datetime as dt
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gold.etiquetas_impago import (  # noqa: E402
    COLUMNAS_ETIQUETAS,
    COLUMNAS_LATENTES,
    VARIABLES_OBSERVADAS,
    calibrar_b0,
    cargar_config,
    estandarizar_variables,
    generar_etiquetas,
    simular_evento,
    verificar_sin_latentes,
)
from src.gold.risk_features import (  # noqa: E402
    KPI_CATEGORICO,
    KPIS_NUMERICOS,
    preparar_para_modelo,
)

CONFIG_REPO = Path(__file__).resolve().parents[1] / "config" / "etiquetas_impago.yaml"
FECHA_OBSERVACION = dt.date(2026, 9, 30)

# Margen entre el AUC del oraculo y el del modelo. Medido con la configuracion
# del repo sobre estas features sinteticas de 5,000 clientes: oraculo 0.825,
# modelo 0.755, hueco 0.070. Se pide 0.02, menos de la mitad, para que la prueba
# falle solo si el hueco desaparece de verdad y no por ruido de muestreo.
MARGEN_ORACULO = 0.02


def _features_sinteticas(n: int, semilla: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(semilla)
    ratio = np.where(rng.random(n) < 0.33, 0.0, rng.lognormal(0.0, 0.9, n))
    uso = np.where(rng.random(n) < 0.46, 0.0, rng.beta(2.0, 4.0, n) * 0.6)
    ahorro = rng.normal(7000.0, 4300.0, n)
    return pd.DataFrame(
        {
            "ratio_endeudamiento": ratio,
            "uso_linea_credito": uso,
            "capacidad_ahorro": ahorro,
        },
        index=pd.Index([f"C{i:06d}" for i in range(n)], name="cliente_id"),
    )


@pytest.fixture(scope="module")
def cfg():
    return cargar_config(CONFIG_REPO)


@pytest.fixture(scope="module")
def features_5k():
    return _features_sinteticas(5000)


@pytest.fixture(scope="module")
def estandarizadas_5k(features_5k, cfg):
    return estandarizar_variables(features_5k, cfg.winsor_inferior, cfg.winsor_superior)


@pytest.fixture(scope="module")
def simulacion_5k(estandarizadas_5k, cfg):
    return simular_evento(estandarizadas_5k, cfg)


# --- Configuracion -----------------------------------------------------------


def test_la_configuracion_del_repo_es_valida(cfg):
    assert cfg.horizonte_meses == 6
    assert cfg.prob_choque_horizonte == pytest.approx(1 - 0.98**6)
    assert cfg.b_ratio_endeudamiento > cfg.b_capacidad_ahorro > cfg.b_uso_linea_credito


@pytest.mark.parametrize(
    "cambio, mensaje",
    [
        ("tasa_objetivo: 1.5", "tasa_objetivo"),
        ("q_choque_mensual: -0.1", "q_choque_mensual"),
        ("horizonte_meses: 0", "horizonte_meses"),
        ("winsor_inferior: 0.99", "winsor_inferior"),
    ],
)
def test_la_configuracion_rechaza_valores_fuera_de_rango(tmp_path, cambio, mensaje):
    llave = cambio.split(":")[0]
    lineas = [
        cambio if linea.startswith(f"{llave}:") else linea
        for linea in CONFIG_REPO.read_text(encoding="utf-8").splitlines()
    ]
    ruta = tmp_path / "config.yaml"
    ruta.write_text("\n".join(lineas), encoding="utf-8")

    with pytest.raises(ValueError, match=mensaje):
        cargar_config(ruta)


def test_la_configuracion_sin_una_llave_falla_con_su_nombre(tmp_path):
    texto = CONFIG_REPO.read_text(encoding="utf-8").replace(
        "tasa_objetivo", "otra_cosa"
    )
    ruta = tmp_path / "config.yaml"
    ruta.write_text(texto, encoding="utf-8")

    with pytest.raises(ValueError, match="tasa_objetivo"):
        cargar_config(ruta)


# --- Estandarizacion ---------------------------------------------------------


def test_cada_variable_queda_con_media_cero_y_desviacion_uno(estandarizadas_5k):
    assert estandarizadas_5k.mean().abs().max() < 1e-9
    assert (estandarizadas_5k.std(ddof=0) - 1.0).abs().max() < 1e-9


def test_la_winsorizacion_recorta_las_colas(features_5k, estandarizadas_5k):
    crudo = features_5k["capacidad_ahorro"]
    sin_recortar = (crudo - crudo.mean()) / crudo.std(ddof=0)
    # con colas gaussianas p1/p99 recortan: el extremo estandarizado es menor
    assert estandarizadas_5k["capacidad_ahorro"].abs().max() < sin_recortar.abs().max()


def test_sin_producto_queda_en_el_valor_minimo(features_5k, estandarizadas_5k):
    sin_producto = features_5k["ratio_endeudamiento"] == 0.0
    con_producto = ~sin_producto
    assert sin_producto.sum() > 0
    assert (
        estandarizadas_5k.loc[sin_producto, "ratio_endeudamiento"].max()
        < estandarizadas_5k.loc[con_producto, "ratio_endeudamiento"].min()
    )


def test_un_faltante_en_producto_equivale_a_cero(features_5k, cfg):
    con_nan = features_5k.copy()
    con_nan.iloc[:50, con_nan.columns.get_loc("ratio_endeudamiento")] = np.nan
    con_cero = features_5k.copy()
    con_cero.iloc[:50, con_cero.columns.get_loc("ratio_endeudamiento")] = 0.0

    pd.testing.assert_frame_equal(
        estandarizar_variables(con_nan, cfg.winsor_inferior, cfg.winsor_superior),
        estandarizar_variables(con_cero, cfg.winsor_inferior, cfg.winsor_superior),
    )


def test_un_faltante_en_capacidad_de_ahorro_es_un_error(features_5k, cfg):
    roto = features_5k.copy()
    roto.iloc[0, roto.columns.get_loc("capacidad_ahorro")] = np.nan
    with pytest.raises(ValueError, match="capacidad_ahorro"):
        estandarizar_variables(roto, cfg.winsor_inferior, cfg.winsor_superior)


# --- Calibracion y tasa ------------------------------------------------------


def test_calibrar_b0_sin_senal_da_el_logit_de_la_tasa():
    b0 = calibrar_b0(np.zeros(100), 0.15)
    assert b0 == pytest.approx(np.log(0.15 / 0.85), abs=1e-9)


def test_la_probabilidad_media_es_exactamente_la_tasa_objetivo(simulacion_5k, cfg):
    assert simulacion_5k.proceso["p"].mean() == pytest.approx(
        cfg.tasa_objetivo, abs=1e-9
    )


def test_la_tasa_realizada_cae_dentro_de_la_tolerancia(simulacion_5k, cfg):
    n = len(simulacion_5k.proceso)
    tolerancia = 4 * np.sqrt(cfg.tasa_objetivo * (1 - cfg.tasa_objetivo) / n)
    assert abs(simulacion_5k.tasa_realizada - cfg.tasa_objetivo) <= tolerancia


# --- Determinismo ------------------------------------------------------------


def test_misma_semilla_produce_las_mismas_etiquetas(features_5k, cfg):
    primera, _, _ = generar_etiquetas(features_5k, cfg, FECHA_OBSERVACION)
    segunda, _, _ = generar_etiquetas(features_5k, cfg, FECHA_OBSERVACION)
    pd.testing.assert_frame_equal(primera, segunda)


def test_otra_semilla_cambia_las_etiquetas(features_5k, cfg):
    otra = replace(cfg, semilla=cfg.semilla + 1)
    primera, _, _ = generar_etiquetas(features_5k, cfg, FECHA_OBSERVACION)
    distinta, _, _ = generar_etiquetas(features_5k, otra, FECHA_OBSERVACION)
    assert not primera["impago_posterior"].equals(distinta["impago_posterior"])


def test_el_resultado_no_depende_del_orden_de_las_filas(features_5k, cfg):
    base, _, _ = generar_etiquetas(features_5k, cfg, FECHA_OBSERVACION)
    barajadas = features_5k.sample(frac=1.0, random_state=3)
    barajado, _, _ = generar_etiquetas(barajadas, cfg, FECHA_OBSERVACION)
    pd.testing.assert_frame_equal(base, barajado)


# --- Esquema: nada del proceso latente en Gold ni en el modelo --------------


def test_la_tabla_de_gold_solo_tiene_la_etiqueta_y_su_fecha(features_5k, cfg):
    tabla, _, _ = generar_etiquetas(features_5k, cfg, FECHA_OBSERVACION)

    assert list(tabla.columns) == COLUMNAS_ETIQUETAS
    assert not set(tabla.columns) & set(COLUMNAS_LATENTES)
    assert set(tabla["impago_posterior"].unique()) <= {0, 1}
    assert (tabla["fecha_observacion"] == FECHA_OBSERVACION).all()
    assert (tabla["horizonte_meses"] == cfg.horizonte_meses).all()
    assert tabla["cliente_id"].is_unique


def test_la_auditoria_si_guarda_el_proceso_latente(features_5k, cfg):
    _, auditoria, _ = generar_etiquetas(features_5k, cfg, FECHA_OBSERVACION)
    assert {"cliente_id", *COLUMNAS_LATENTES} == set(auditoria.columns)


def test_el_feature_store_real_no_define_columnas_latentes():
    columnas_reales = [*KPIS_NUMERICOS, KPI_CATEGORICO]
    verificar_sin_latentes(columnas_reales, "KPIS_NUMERICOS + KPI_CATEGORICO")


def test_la_matriz_del_modelo_no_contiene_columnas_latentes(features_5k):
    features = features_5k.assign(categoria_gasto_dominante="alimentos")
    matriz = preparar_para_modelo(features)
    verificar_sin_latentes(matriz.columns, "la matriz de entrenamiento")


@pytest.mark.parametrize("columna", COLUMNAS_LATENTES)
def test_una_columna_latente_en_las_features_se_rechaza(features_5k, cfg, columna):
    contaminadas = features_5k.assign(**{columna: 0.0})
    with pytest.raises(ValueError, match="proceso latente"):
        generar_etiquetas(contaminadas, cfg, FECHA_OBSERVACION)


# --- Recuperacion de signos y comparacion con el oraculo ---------------------


def test_la_regresion_logistica_recupera_los_signos(estandarizadas_5k, simulacion_5k):
    y = simulacion_5k.proceso["impago"].to_numpy()
    modelo = LogisticRegression(C=1e6, max_iter=1000).fit(
        estandarizadas_5k.loc[simulacion_5k.proceso.index, VARIABLES_OBSERVADAS], y
    )
    coef = dict(zip(VARIABLES_OBSERVADAS, modelo.coef_[0]))

    assert coef["ratio_endeudamiento"] > 0
    assert coef["uso_linea_credito"] > 0
    assert coef["capacidad_ahorro"] < 0


def test_el_auc_del_modelo_queda_por_debajo_del_del_oraculo(
    estandarizadas_5k, simulacion_5k
):
    y = simulacion_5k.proceso["impago"].to_numpy()
    p_verdadera = simulacion_5k.proceso["p"].to_numpy()
    observadas = estandarizadas_5k.loc[
        simulacion_5k.proceso.index, VARIABLES_OBSERVADAS
    ]

    auc_oraculo = roc_auc_score(y, p_verdadera)
    predicciones = cross_val_predict(
        HistGradientBoostingClassifier(max_depth=3, random_state=0),
        observadas,
        y,
        cv=StratifiedKFold(5, shuffle=True, random_state=0),
        method="predict_proba",
    )[:, 1]
    auc_modelo = roc_auc_score(y, predicciones)

    assert auc_oraculo > 0.6, "el oraculo debe tener senal real"
    assert auc_modelo < auc_oraculo - MARGEN_ORACULO, (
        f"AUC del modelo {auc_modelo:.3f} no queda {MARGEN_ORACULO} por debajo "
        f"del oraculo {auc_oraculo:.3f}"
    )

"""
tests/test_psi.py

Pruebas del monitoreo de deriva con PSI: el calculo (src/monitoreo/psi.py),
la referencia congelada (src/monitoreo/referencia.py) y el paso completo
(monitor_drift.py) sobre un Gold minimo en un DuckDB temporal. No necesitan
Spark ni XGBoost: el modelo de prueba es una regresion logistica de
scikit-learn, que expone la misma interfaz (predict_proba y
feature_names_in_) que usa el monitoreo.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor_drift  # noqa: E402
from src.gold.risk_features import preparar_para_modelo  # noqa: E402
from src.monitoreo.psi import (  # noqa: E402
    ConfigPSI,
    calcular_cortes,
    cargar_config,
    clasificar,
    proporciones,
    psi,
)
from src.monitoreo.referencia import (  # noqa: E402
    ARCHIVO_MODELO,
    ARCHIVO_REFERENCIA,
    cargar_activa,
    crear_referencia,
    id_activa,
)

EPSILON = 1e-4
CFG = ConfigPSI(
    bins=10,
    epsilon=EPSILON,
    umbral_vigilar=0.10,
    umbral_reentrenar=0.25,
    ruta_referencias="no-se-usa",
    ruta_historico="no-se-usa",
)


def _psi_con_cortes_de(referencia, actual, bins=10):
    cortes = calcular_cortes(referencia, bins)
    return psi(proporciones(referencia, cortes), proporciones(actual, cortes), EPSILON)


# --- Calculo --------------------------------------------------------------


def test_distribuciones_identicas_dan_psi_cero():
    valores = np.random.default_rng(42).normal(15000, 4000, 2000)

    assert _psi_con_cortes_de(valores, valores.copy()) == pytest.approx(0.0, abs=1e-12)


def test_distribucion_desplazada_da_psi_alto():
    rng = np.random.default_rng(42)
    referencia = rng.normal(15000, 4000, 2000)
    desplazada = referencia + 6000  # 1.5 desviaciones estandar

    valor = _psi_con_cortes_de(referencia, desplazada)

    assert valor > 0.25
    assert clasificar(valor, CFG) == "reentrenar"


def test_bins_vacios_no_fallan_y_dan_un_valor_finito():
    referencia = np.arange(1000, dtype=float)
    # Todos los valores actuales caen en el ultimo intervalo: los otros 9
    # quedan vacios en la distribucion actual.
    actual = np.full(200, 5000.0)

    valor = _psi_con_cortes_de(referencia, actual)

    assert np.isfinite(valor)
    assert valor > 0.25


def test_un_intervalo_vacio_en_ambas_distribuciones_no_aporta():
    # El intervalo de faltantes esta vacio en las dos: con epsilon en ambos
    # lados su termino es (eps - eps) * ln(1) = 0.
    assert psi([0.5, 0.5, 0.0], [0.5, 0.5, 0.0], EPSILON) == 0.0


def test_variable_binaria_no_produce_intervalos_de_ancho_cero():
    binaria = np.array([0] * 700 + [1] * 300, dtype=float)

    cortes = calcular_cortes(binaria, 10)

    assert cortes == sorted(set(cortes))
    assert _psi_con_cortes_de(binaria, binaria) == pytest.approx(0.0, abs=1e-12)


def test_los_faltantes_tienen_su_propio_intervalo():
    referencia = np.arange(100, dtype=float)
    con_faltantes = np.append(np.arange(80, dtype=float), [np.nan] * 20)

    cortes = calcular_cortes(referencia, 10)
    actual = proporciones(con_faltantes, cortes)

    assert actual[-1] == pytest.approx(0.20)
    assert actual.sum() == pytest.approx(1.0)


def test_umbrales_en_los_limites():
    assert clasificar(0.0999, CFG) == "estable"
    assert clasificar(0.10, CFG) == "vigilar"
    assert clasificar(0.25, CFG) == "vigilar"
    assert clasificar(0.2501, CFG) == "reentrenar"


@pytest.mark.parametrize(
    "umbrales, mensaje",
    [
        ({"vigilar": 0.25, "reentrenar": 0.10}, "vigilar < umbrales.reentrenar"),
        ({"vigilar": float("nan"), "reentrenar": 0.25}, "finito"),
    ],
)
def test_config_rechaza_umbrales_invalidos(tmp_path, umbrales, mensaje):
    ruta = tmp_path / "psi.yaml"
    ruta.write_text(
        json.dumps(
            {
                "bins": 10,
                "epsilon": 0.0001,
                "umbrales": umbrales,
                "rutas": {"referencias": "r", "historico": "h"},
            }
        ).replace("NaN", ".nan"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=mensaje):
        cargar_config(ruta)


def test_config_del_repo_es_valida():
    cfg = cargar_config(
        Path(__file__).resolve().parents[1] / "config/monitoreo_psi.yaml"
    )

    assert (cfg.umbral_vigilar, cfg.umbral_reentrenar) == (0.10, 0.25)


# --- Referencia congelada ---------------------------------------------------


def _modelo_falso(tmp_path: Path) -> Path:
    ruta = tmp_path / "modelo.joblib"
    ruta.write_bytes(b"modelo de prueba")
    return ruta


def _matriz(n=300, semilla=42) -> pd.DataFrame:
    rng = np.random.default_rng(semilla)
    return pd.DataFrame(
        {
            "ingreso_mensual_promedio": rng.normal(15000, 4000, n),
            "categoria_dominante_comida": rng.integers(0, 2, n).astype(bool),
        }
    )


def test_la_referencia_guarda_cortes_y_proporciones_sin_la_columna_onehot(tmp_path):
    raiz = tmp_path / "referencias"
    matriz = _matriz()

    ref_id = crear_referencia(
        raiz, matriz, np.linspace(0, 1, len(matriz)), _modelo_falso(tmp_path), 10, None
    )

    contenido, ruta_modelo = cargar_activa(raiz)
    assert contenido["id"] == ref_id
    assert list(contenido["variables"]) == ["ingreso_mensual_promedio"]
    assert set(contenido["variables"]["ingreso_mensual_promedio"]) == {
        "cortes",
        "proporciones",
    }
    assert ruta_modelo.read_bytes() == b"modelo de prueba"


def test_nueva_referencia_archiva_la_anterior_y_no_la_sobrescribe(tmp_path):
    raiz = tmp_path / "referencias"
    modelo = _modelo_falso(tmp_path)
    primera = crear_referencia(
        raiz, _matriz(), np.zeros(300), modelo, 10, None, ahora=datetime(2026, 10, 1)
    )
    contenido_primera = (raiz / primera / ARCHIVO_REFERENCIA).read_bytes()

    segunda = crear_referencia(
        raiz,
        _matriz(semilla=7),
        np.ones(300),
        modelo,
        10,
        None,
        ahora=datetime(2026, 11, 1),
    )

    assert id_activa(raiz) == segunda
    assert json.loads((raiz / "activa.json").read_text())["anterior"] == primera
    assert (raiz / primera / ARCHIVO_REFERENCIA).read_bytes() == contenido_primera
    with pytest.raises(FileExistsError):
        crear_referencia(
            raiz,
            _matriz(),
            np.zeros(300),
            modelo,
            10,
            None,
            ahora=datetime(2026, 11, 1),
        )


def test_un_modelo_base_alterado_se_rechaza(tmp_path):
    raiz = tmp_path / "referencias"
    ref_id = crear_referencia(
        raiz, _matriz(), np.zeros(300), _modelo_falso(tmp_path), 10, None
    )
    (raiz / ref_id / ARCHIVO_MODELO).write_bytes(b"otro modelo")

    with pytest.raises(ValueError, match="no coincide con el hash"):
        cargar_activa(raiz)


# --- Paso completo (monitor_drift.py) ---------------------------------------


def _features(n=400, semilla=42, desplazamiento=0.0) -> pd.DataFrame:
    rng = np.random.default_rng(semilla)
    return pd.DataFrame(
        {
            "cliente_id": [f"CLI-{i:06d}" for i in range(n)],
            "ingreso_mensual_promedio": rng.normal(15000, 4000, n) + desplazamiento,
            "ratio_endeudamiento": rng.uniform(0, 3, n),
            "categoria_gasto_dominante": rng.choice(["comida", "hogar", "otros"], n),
            "fecha_corte": pd.Timestamp("2026-09-30").date(),
            "fecha_calculo": pd.Timestamp("2026-10-16"),
        }
    )


def _escribir_features(gold: str, features: pd.DataFrame):
    con = duckdb.connect(gold)
    con.register("f", features)
    con.execute("CREATE OR REPLACE TABLE gold_features_cliente AS SELECT * FROM f")
    con.close()


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Gold con features y etiquetas, un modelo entrenado sobre ellas y una
    config que apunta a carpetas temporales."""
    gold = str(tmp_path / "kpis.duckdb")
    features = _features()
    _escribir_features(gold, features)
    etiquetas = pd.DataFrame(
        {
            "cliente_id": features["cliente_id"],
            "impago_posterior": (features["ratio_endeudamiento"] > 2).astype(int),
        }
    )
    con = duckdb.connect(gold)
    con.register("e", etiquetas)
    con.execute("CREATE TABLE gold_etiquetas_impago AS SELECT * FROM e")
    con.close()

    x = preparar_para_modelo(features.set_index("cliente_id"))
    modelo = LogisticRegression(max_iter=1000).fit(
        x, etiquetas["impago_posterior"].values
    )
    ruta_modelo = tmp_path / "risk_model.joblib"
    joblib.dump(modelo, ruta_modelo)

    config = tmp_path / "monitoreo_psi.yaml"
    config.write_text(
        "bins: 10\nepsilon: 0.0001\numbrales: {vigilar: 0.10, reentrenar: 0.25}\n"
        f"rutas: {{referencias: {tmp_path / 'referencia_psi'}, "
        f"historico: {tmp_path / 'deriva_psi'}}}\n",
        encoding="utf-8",
    )

    def correr(*extra):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "monitor_drift.py",
                "--gold",
                gold,
                "--model",
                str(ruta_modelo),
                "--config",
                str(config),
                "--logs",
                str(tmp_path / "logs"),
                *extra,
            ],
        )
        monitor_drift.main()

    return {"gold": gold, "tmp": tmp_path, "correr": correr, "modelo": ruta_modelo}


def _historico(tmp_path: Path) -> pd.DataFrame:
    return pd.concat(
        pd.read_parquet(p) for p in sorted((tmp_path / "deriva_psi").glob("*.parquet"))
    )


def test_primera_corrida_crea_la_referencia_y_da_psi_cero(entorno):
    entorno["correr"]()

    historico = _historico(entorno["tmp"])
    assert set(historico["variable"]) == {
        "ingreso_mensual_promedio",
        "ratio_endeudamiento",
        "probabilidad_impago",
    }
    assert historico["psi"].abs().max() < 1e-12
    assert set(historico["estado"]) == {"estable"}
    assert id_activa(entorno["tmp"] / "referencia_psi") is not None


def test_el_historico_tiene_el_esquema_explicito(entorno):
    entorno["correr"]()

    archivo = next((entorno["tmp"] / "deriva_psi").glob("*.parquet"))
    esquema = pq.read_schema(archivo)
    assert esquema.names == monitor_drift.ESQUEMA_HISTORICO.names
    assert (
        esquema.field("psi").type == monitor_drift.ESQUEMA_HISTORICO.field("psi").type
    )


def test_las_corridas_siguientes_reutilizan_los_cortes_de_entrenamiento(
    entorno, caplog
):
    entorno["correr"]()
    raiz = entorno["tmp"] / "referencia_psi"
    ref_id = id_activa(raiz)
    referencia_antes = (raiz / ref_id / ARCHIVO_REFERENCIA).read_bytes()

    # Llegan datos desplazados y el DAG reentrena: el modelo actual cambia,
    # pero la referencia y su modelo base no.
    _escribir_features(entorno["gold"], _features(semilla=1, desplazamiento=8000))
    entorno["modelo"].write_bytes(b"un modelo reentrenado que no debe usarse")
    with caplog.at_level(logging.WARNING):
        entorno["correr"]()

    assert id_activa(raiz) == ref_id
    assert (raiz / ref_id / ARCHIVO_REFERENCIA).read_bytes() == referencia_antes
    ultima = (
        _historico(entorno["tmp"]).query("run_id == run_id.max()").set_index("variable")
    )
    assert ultima.loc["ingreso_mensual_promedio", "estado"] == "reentrenar"
    assert ultima.loc["ratio_endeudamiento", "estado"] == "estable"
    assert "ALERTA deriva: ingreso_mensual_promedio" in caplog.text

    # Contraste: si se recalcularan los deciles con los datos nuevos, cada
    # intervalo volveria a tener ~10 %, igual que las proporciones guardadas,
    # y el mismo desplazamiento pasaria inadvertido.
    guardadas = json.loads(referencia_antes)["variables"]["ingreso_mensual_promedio"]
    nuevos = _features(semilla=1, desplazamiento=8000)["ingreso_mensual_promedio"]
    recalculado = psi(
        guardadas["proporciones"],
        proporciones(nuevos, calcular_cortes(nuevos, 10)),
        EPSILON,
    )
    assert recalculado < 0.10 < ultima.loc["ingreso_mensual_promedio", "psi"]


def test_nueva_referencia_desde_el_cli_archiva_la_activa(entorno):
    entorno["correr"]()
    raiz = entorno["tmp"] / "referencia_psi"
    primera = id_activa(raiz)

    # Otra referencia en el mismo segundo chocaria de nombre: se fija el reloj.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "src.monitoreo.referencia.datetime",
            type("Reloj", (), {"now": staticmethod(lambda: datetime(2030, 1, 1))}),
        )
        entorno["correr"]("--nueva-referencia")

    assert id_activa(raiz) == "ref_20300101_000000"
    assert (raiz / primera / ARCHIVO_REFERENCIA).exists()


def test_una_referencia_solo_con_faltantes_mide_el_cambio_de_nulos():
    referencia = np.full(100, np.nan)

    cortes = calcular_cortes(referencia, 10)

    assert cortes == []
    assert (
        psi(proporciones(referencia, cortes), proporciones(referencia, cortes), EPSILON)
        == 0.0
    )
    assert (
        psi(
            proporciones(referencia, cortes),
            proporciones(np.ones(100), cortes),
            EPSILON,
        )
        > 0.25
    )


def test_variables_temporales_se_reportan_sin_alerta(entorno, caplog):
    """Una variable marcada como temporal guarda su PSI aunque sea alto, pero
    queda en estado "temporal" y no genera alerta."""
    config = entorno["tmp"] / "monitoreo_psi.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "variables_temporales: [ingreso_mensual_promedio]\n",
        encoding="utf-8",
    )
    entorno["correr"]()
    _escribir_features(entorno["gold"], _features(semilla=1, desplazamiento=8000))
    with caplog.at_level(logging.WARNING):
        entorno["correr"]()

    ultima = (
        _historico(entorno["tmp"]).query("run_id == run_id.max()").set_index("variable")
    )
    assert ultima.loc["ingreso_mensual_promedio", "estado"] == "temporal"
    assert ultima.loc["ingreso_mensual_promedio", "psi"] > 0.25
    assert "ALERTA deriva: ingreso_mensual_promedio" not in caplog.text
    # El score nunca es temporal, aunque se llame como una variable listada.
    assert ultima.loc["probabilidad_impago", "estado"] != "temporal"


def test_variable_temporal_inexistente_avisa(entorno, caplog):
    config = entorno["tmp"] / "monitoreo_psi.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "variables_temporales: [antiguedad_dia]\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        entorno["correr"]()
    assert "antiguedad_dia" in caplog.text and "revisa el nombre" in caplog.text


def test_config_del_repo_marca_las_variables_temporales():
    cfg = cargar_config(
        Path(__file__).resolve().parents[1] / "config/monitoreo_psi.yaml"
    )

    assert set(cfg.variables_temporales) == {
        "antiguedad_dias",
        "edad_anios",
        "proporcion_retiros",
    }


def test_config_rechaza_variables_temporales_invalidas(tmp_path):
    ruta = tmp_path / "psi.yaml"
    ruta.write_text(
        "bins: 10\nepsilon: 0.0001\numbrales: {vigilar: 0.10, reentrenar: 0.25}\n"
        "rutas: {referencias: r, historico: h}\nvariables_temporales: [1, '']\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="variables_temporales"):
        cargar_config(ruta)

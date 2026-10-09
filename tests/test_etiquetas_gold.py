"""
tests/test_etiquetas_gold.py

Pruebas de la lectura y escritura de las etiquetas de impago en Gold
(src/gold/etiquetas_gold.py) y del flujo completo de generate_labels.py, sobre
un DuckDB temporal. El feature store se escribe con escribir_features(), la
misma funcion que usa el pipeline, para probar contra el esquema real y no
contra uno supuesto. No necesitan Spark ni XGBoost.
"""

import datetime as dt
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_labels  # noqa: E402
from src.gold.etiquetas_gold import (  # noqa: E402
    cargar_etiquetas,
    escribir_auditoria,
    escribir_etiquetas,
    verificar_esquema_gold,
)
from src.gold.etiquetas_impago import (  # noqa: E402
    COLUMNAS_ETIQUETAS,
    COLUMNAS_LATENTES,
    TABLA_ETIQUETAS,
)
from src.gold.risk_features import TABLA_FEATURES, escribir_features  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "config" / "etiquetas_impago.yaml"
FECHA_CORTE = dt.date(2026, 9, 30)
N = 400


def _features(con_fecha_corte: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    features = pd.DataFrame(
        {
            "ingreso_mensual_promedio": rng.normal(20000, 6000, N),
            "ratio_endeudamiento": np.where(
                rng.random(N) < 0.33, 0.0, rng.lognormal(0.0, 0.9, N)
            ),
            "uso_linea_credito": np.where(
                rng.random(N) < 0.46, 0.0, rng.beta(2, 4, N) * 0.6
            ),
            "capacidad_ahorro": rng.normal(7000, 4300, N),
            "categoria_gasto_dominante": "supermercado",
        },
        index=pd.Index([f"CLI-{i:06d}" for i in range(N)], name="cliente_id"),
    )
    if con_fecha_corte:
        features["fecha_corte"] = FECHA_CORTE
    return features


@pytest.fixture
def gold(tmp_path):
    ruta = str(tmp_path / "kpis.duckdb")
    escribir_features(_features(), ruta)
    con = duckdb.connect(ruta)
    con.execute(
        "CREATE TABLE gold_kpis AS SELECT 'CLI-000000' AS cliente_id, 1.0 AS valor"
    )
    con.close()
    return ruta


def _correr(gold, tmp_path, monkeypatch):
    auditoria = tmp_path / "auditoria" / "etiquetas_latentes.parquet"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_labels.py",
            "--gold",
            gold,
            "--config",
            str(CONFIG),
            "--auditoria-out",
            str(auditoria),
        ],
    )
    generate_labels.main()
    return auditoria


def _tablas(gold):
    con = duckdb.connect(gold, read_only=True)
    try:
        return {fila[0] for fila in con.execute("SHOW TABLES").fetchall()}
    finally:
        con.close()


def test_el_flujo_completo_escribe_la_tabla_y_la_auditoria(gold, tmp_path, monkeypatch):
    auditoria = _correr(gold, tmp_path, monkeypatch)

    etiquetas = cargar_etiquetas(gold)
    assert list(etiquetas.columns) == COLUMNAS_ETIQUETAS
    assert len(etiquetas) == N
    assert pd.to_datetime(etiquetas["fecha_observacion"]).dt.date.unique().tolist() == [
        FECHA_CORTE
    ]
    assert set(etiquetas["impago_posterior"].unique()) == {0, 1}

    audit = pd.read_parquet(auditoria)
    assert set(audit.columns) == {"cliente_id", *COLUMNAS_LATENTES}
    assert set(audit["cliente_id"]) == set(etiquetas["cliente_id"])


def test_la_auditoria_queda_fuera_de_gold(gold, tmp_path, monkeypatch):
    _correr(gold, tmp_path, monkeypatch)

    assert _tablas(gold) == {TABLA_FEATURES, "gold_kpis", TABLA_ETIQUETAS}
    esquema = verificar_esquema_gold(gold)
    for columnas in esquema.values():
        assert not set(columnas) & set(COLUMNAS_LATENTES)


def test_correr_dos_veces_reemplaza_y_no_duplica(gold, tmp_path, monkeypatch):
    _correr(gold, tmp_path, monkeypatch)
    primera = cargar_etiquetas(gold)
    _correr(gold, tmp_path, monkeypatch)
    segunda = cargar_etiquetas(gold)

    assert len(segunda) == N
    pd.testing.assert_frame_equal(primera, segunda)


def test_no_toca_las_otras_tablas_de_gold(gold, tmp_path, monkeypatch):
    con = duckdb.connect(gold, read_only=True)
    antes = con.execute(f"SELECT * FROM {TABLA_FEATURES} ORDER BY cliente_id").fetchdf()
    con.close()

    _correr(gold, tmp_path, monkeypatch)

    con = duckdb.connect(gold, read_only=True)
    despues = con.execute(
        f"SELECT * FROM {TABLA_FEATURES} ORDER BY cliente_id"
    ).fetchdf()
    kpis = con.execute("SELECT COUNT(*) FROM gold_kpis").fetchone()[0]
    con.close()
    pd.testing.assert_frame_equal(antes, despues)
    assert kpis == 1


def test_sin_fecha_de_corte_falla_diciendo_como_arreglarlo(tmp_path, monkeypatch):
    ruta = str(tmp_path / "kpis.duckdb")
    escribir_features(_features(con_fecha_corte=False), ruta)

    with pytest.raises(RuntimeError, match="fecha_corte"):
        _correr(ruta, tmp_path, monkeypatch)


def test_cargar_etiquetas_sin_tabla_da_error_claro(tmp_path):
    ruta = str(tmp_path / "vacia.duckdb")
    duckdb.connect(ruta).close()

    with pytest.raises(RuntimeError, match="generate_labels"):
        cargar_etiquetas(ruta)


def test_escribir_etiquetas_rechaza_columnas_del_proceso_latente(tmp_path):
    tabla = pd.DataFrame(
        {
            "cliente_id": ["a"],
            "impago_posterior": [1],
            "fecha_observacion": [FECHA_CORTE],
            "horizonte_meses": [6],
            "z": [0.3],
        }
    )
    with pytest.raises(ValueError, match="proceso latente"):
        escribir_etiquetas(tabla, str(tmp_path / "kpis.duckdb"))


def test_escribir_etiquetas_rechaza_un_esquema_distinto(tmp_path):
    tabla = pd.DataFrame({"cliente_id": ["a"], "impago_posterior": [1]})
    with pytest.raises(ValueError, match="exactamente"):
        escribir_etiquetas(tabla, str(tmp_path / "kpis.duckdb"))


def test_verificar_esquema_gold_detecta_una_columna_latente_en_cualquier_tabla(gold):
    con = duckdb.connect(gold)
    con.execute("CREATE TABLE otra_tabla AS SELECT 'a' AS cliente_id, 0.5 AS p")
    con.close()

    with pytest.raises(ValueError, match="otra_tabla"):
        verificar_esquema_gold(gold)


def test_escribir_auditoria_crea_la_carpeta(tmp_path):
    auditoria = pd.DataFrame(
        {"cliente_id": ["a"], "z": [0.1], "choque": [0], "logit": [-2.0], "p": [0.12]}
    )
    ruta = escribir_auditoria(
        auditoria, tmp_path / "nueva" / "carpeta" / "audit.parquet"
    )

    assert ruta.exists()
    pd.testing.assert_frame_equal(pd.read_parquet(ruta), auditoria)

"""
tests/test_features.py

Pruebas del feature store ligero (src/gold/risk_features.py) en la
parte que no necesita Spark: persistencia en DuckDB y preparacion de
la matriz para el modelo. La construccion desde Silver la cubre el
smoke test de CI.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gold.risk_features import (  # noqa: E402
    TABLA_FEATURES,
    cargar_features,
    escribir_features,
    preparar_para_modelo,
)


@pytest.fixture
def features_legibles():
    """Tabla con la forma que devuelve construir_features(): indexada
    por cliente_id, categoria de gasto como texto."""
    return pd.DataFrame(
        {
            "cliente_id": ["CLI-000001", "CLI-000002", "CLI-000003"],
            "ingreso_mensual_promedio": [25000.0, 18000.0, 40000.0],
            "ratio_endeudamiento": [0.4, 0.0, 1.2],
            "categoria_gasto_dominante": [
                "supermercado",
                "restaurantes",
                "supermercado",
            ],
            "edad_anios": [31.5, 24.2, 45.0],
            "numero_productos": [2.0, 1.0, 3.0],
        }
    ).set_index("cliente_id")


def test_escribir_y_cargar_conserva_filas_y_columnas(tmp_path, features_legibles):
    gold = str(tmp_path / "kpis.duckdb")

    n_filas = escribir_features(features_legibles, gold)
    cargadas = cargar_features(gold)

    assert n_filas == 3
    assert list(cargadas.index) == list(features_legibles.index)
    assert "fecha_calculo" in cargadas.columns
    assert cargadas.loc["CLI-000003", "categoria_gasto_dominante"] == "supermercado"


def test_escribir_reemplaza_la_corrida_anterior(tmp_path, features_legibles):
    gold = str(tmp_path / "kpis.duckdb")

    escribir_features(features_legibles, gold)
    escribir_features(features_legibles.head(1), gold)

    assert len(cargar_features(gold)) == 1


def test_cargar_sin_tabla_da_error_claro(tmp_path):
    import duckdb

    gold = str(tmp_path / "kpis.duckdb")
    duckdb.connect(gold).close()

    with pytest.raises(RuntimeError, match=TABLA_FEATURES):
        cargar_features(gold)


def test_preparar_codifica_categoria_y_quita_metadata(tmp_path, features_legibles):
    gold = str(tmp_path / "kpis.duckdb")
    escribir_features(features_legibles, gold)

    matriz = preparar_para_modelo(cargar_features(gold))

    assert "fecha_calculo" not in matriz.columns
    assert "categoria_gasto_dominante" not in matriz.columns
    assert {
        "categoria_dominante_supermercado",
        "categoria_dominante_restaurantes",
    } <= set(matriz.columns)


def test_preparar_alinea_a_columnas_del_modelo(features_legibles):
    """En prediccion: una categoria nueva se descarta, una que el modelo
    vio pero no aparece se rellena con 0, y el orden es el del modelo."""
    columnas_modelo = [
        "categoria_dominante_entretenimiento",
        "edad_anios",
        "categoria_dominante_supermercado",
        "ingreso_mensual_promedio",
        "numero_productos",
        "ratio_endeudamiento",
    ]

    matriz = preparar_para_modelo(features_legibles, columnas_modelo=columnas_modelo)

    assert list(matriz.columns) == columnas_modelo
    assert "categoria_dominante_restaurantes" not in matriz.columns
    assert (matriz["categoria_dominante_entretenimiento"] == 0).all()

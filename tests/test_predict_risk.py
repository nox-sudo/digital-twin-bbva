"""
tests/test_predict_risk.py

Pruebas del borde final de inferencia (predict_risk.py): como se
escribe probabilidad_impago en Gold. No necesitan un modelo entrenado
ni Spark: trabajan sobre un gold_kpis minimo en un DuckDB temporal.
"""

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from predict_risk import actualizar_gold, escribir_parquet_atomico  # noqa: E402


@pytest.fixture
def gold_con_nulos(tmp_path):
    """gold_kpis como lo deja transform_gold.py: un KPI normal y
    probabilidad_impago en NULL para dos clientes."""
    ruta = str(tmp_path / "kpis.duckdb")
    filas = pd.DataFrame(
        {
            "cliente_id": ["CLI-000001", "CLI-000002"] * 2,
            "kpi_id": ["capacidad_ahorro"] * 2 + ["probabilidad_impago"] * 2,
            "categoria": ["ahorro_y_liquidez"] * 2 + ["riesgo_y_endeudamiento"] * 2,
            "nombre": ["Capacidad de ahorro"] * 2 + ["Probabilidad de impago"] * 2,
            "valor_numerico": [1500.0, -200.0, None, None],
            "valor_texto": [None] * 4,
            "fecha_calculo": [pd.Timestamp("2026-10-01")] * 4,
        }
    )
    con = duckdb.connect(ruta)
    con.register("filas", filas)
    con.execute("CREATE TABLE gold_kpis AS SELECT * FROM filas")
    con.close()
    return ruta


def _filas_impago(ruta: str) -> pd.DataFrame:
    con = duckdb.connect(ruta, read_only=True)
    try:
        return con.execute(
            "SELECT cliente_id, valor_numerico FROM gold_kpis "
            "WHERE kpi_id = 'probabilidad_impago' ORDER BY cliente_id"
        ).fetchdf()
    finally:
        con.close()


def test_actualizar_gold_reemplaza_los_nulos(gold_con_nulos):
    predicciones = pd.DataFrame(
        {"cliente_id": ["CLI-000001", "CLI-000002"], "valor_numerico": [0.12, 0.87]}
    )

    actualizar_gold(gold_con_nulos, predicciones)

    resultado = _filas_impago(gold_con_nulos)
    assert len(resultado) == 2
    assert resultado["valor_numerico"].tolist() == pytest.approx([0.12, 0.87])


def test_un_fallo_al_insertar_deja_gold_intacto(gold_con_nulos):
    """Si el INSERT falla despues del DELETE, el ROLLBACK debe devolver
    las filas originales. Se provoca el fallo con un valor que no cabe
    en la columna DOUBLE."""
    antes = _filas_impago(gold_con_nulos)
    invalidas = pd.DataFrame(
        {"cliente_id": ["CLI-000001"], "valor_numerico": ["no es un numero"]}
    )

    with pytest.raises(Exception):
        actualizar_gold(gold_con_nulos, invalidas)

    pd.testing.assert_frame_equal(_filas_impago(gold_con_nulos), antes)


def test_parquet_atomico_no_deja_temporales(tmp_path):
    destino = tmp_path / "probabilidad_impago.parquet"
    predicciones = pd.DataFrame({"cliente_id": ["CLI-000001"], "valor_numerico": [0.3]})

    escribir_parquet_atomico(predicciones, destino)

    assert destino.exists()
    assert list(tmp_path.glob("*.tmp")) == []
    pd.testing.assert_frame_equal(pd.read_parquet(destino), predicciones)

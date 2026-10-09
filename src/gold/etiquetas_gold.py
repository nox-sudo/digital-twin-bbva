"""
src/gold/etiquetas_gold.py

Lectura y escritura de las etiquetas de impago en el DuckDB de Gold
(gold_etiquetas_impago) y de la auditoria del proceso latente. Es la capa de
entrada y salida de src/gold/etiquetas_impago.py, que se queda puro.

Dos destinos distintos, a proposito:
- gold_etiquetas_impago (en Gold): solo la etiqueta y su fecha de observacion.
- auditoria (parquet en data/auditoria/): z, choque, logit y p. Nunca en Gold,
  ni en las sesiones, ni en la landing zone, ni en el respaldo: se regenera a
  partir de la semilla y las features, asi que no hace falta conservarlo.

verificar_esquema_gold recorre TODAS las tablas del DuckDB y falla si alguna
tiene una columna del proceso latente. Es la verificacion sobre el esquema real
(la usan generate_labels.py al terminar y el smoke test de CI).
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pandas as pd

from src.gold.etiquetas_impago import (
    COLUMNAS_ETIQUETAS,
    COLUMNAS_LATENTES,
    TABLA_ETIQUETAS,
    verificar_sin_latentes,
)

logger = logging.getLogger(__name__)

RUTA_AUDITORIA = "data/auditoria/etiquetas_latentes.parquet"


def escribir_etiquetas(tabla: pd.DataFrame, gold_duckdb_path: str) -> int:
    """Persiste la tabla de etiquetas en Gold reemplazando la version anterior
    completa (snapshot por corrida, igual que gold_kpis y el feature store). No
    toca ninguna otra tabla. Devuelve el numero de filas escritas."""
    verificar_sin_latentes(tabla.columns, TABLA_ETIQUETAS)
    if list(tabla.columns) != COLUMNAS_ETIQUETAS:
        raise ValueError(
            f"{TABLA_ETIQUETAS} debe tener exactamente {COLUMNAS_ETIQUETAS}, "
            f"recibio {list(tabla.columns)}"
        )

    con = duckdb.connect(gold_duckdb_path)
    try:
        con.register("etiquetas_df", tabla)
        con.execute(
            f"CREATE OR REPLACE TABLE {TABLA_ETIQUETAS} AS SELECT * FROM etiquetas_df"
        )
        n_filas = con.execute(f"SELECT COUNT(*) FROM {TABLA_ETIQUETAS}").fetchone()[0]
    finally:
        con.close()

    logger.info("%s escrita: %d clientes", TABLA_ETIQUETAS, n_filas)
    return n_filas


def cargar_etiquetas(gold_duckdb_path: str) -> pd.DataFrame:
    """Lee gold_etiquetas_impago desde Gold."""
    con = duckdb.connect(gold_duckdb_path, read_only=True)
    try:
        return con.execute(f"SELECT * FROM {TABLA_ETIQUETAS}").fetchdf()
    except duckdb.CatalogException as error:
        raise RuntimeError(
            f"No existe la tabla {TABLA_ETIQUETAS} en {gold_duckdb_path}. "
            "Corre primero generate_labels.py (python main.py labels)."
        ) from error
    finally:
        con.close()


def escribir_auditoria(
    auditoria: pd.DataFrame, ruta: str | Path = RUTA_AUDITORIA
) -> Path:
    """Guarda z, choque, logit y p por cliente, fuera de Gold."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    auditoria.to_parquet(ruta, index=False)
    logger.info(
        "Auditoria del proceso latente escrita en %s (no es parte de Gold)", ruta
    )
    return ruta


def verificar_esquema_gold(gold_duckdb_path: str) -> dict[str, list[str]]:
    """Falla si alguna tabla del DuckDB tiene columnas del proceso latente.
    Devuelve las columnas de cada tabla, para que quien llama pueda reportarlas."""
    con = duckdb.connect(gold_duckdb_path, read_only=True)
    try:
        tablas = [fila[0] for fila in con.execute("SHOW TABLES").fetchall()]
        esquema = {
            tabla: [fila[0] for fila in con.execute(f"DESCRIBE {tabla}").fetchall()]
            for tabla in tablas
        }
    finally:
        con.close()

    for tabla, columnas in esquema.items():
        verificar_sin_latentes(columnas, f"la tabla de Gold {tabla}")
    logger.info(
        "Esquema de Gold verificado: %d tablas, ninguna con %s",
        len(esquema),
        COLUMNAS_LATENTES,
    )
    return esquema

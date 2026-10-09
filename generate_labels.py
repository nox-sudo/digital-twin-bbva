"""
generate_labels.py

Genera la etiqueta de impago de cada cliente como un evento POSTERIOR a las
features, con un modelo latente (src/gold/etiquetas_impago.py), y la guarda en
gold_etiquetas_impago. No existen etiquetas reales: es un proyecto con datos
sinteticos, sin historial de impago observado.

Lee gold_features_cliente (construida por build_features.py) y no necesita
Spark. Escribe:
  - gold_etiquetas_impago en el DuckDB de Gold: cliente_id, impago_posterior,
    fecha_observacion y horizonte_meses. Nada del proceso latente.
  - la auditoria (z, choque, logit, p) en un parquet fuera de Gold, por defecto
    data/auditoria/etiquetas_latentes.parquet. No se respalda: se regenera con
    la semilla y las features.

Reemplaza a la regla "al menos 2 de 3 condiciones" mas el XOR de ruido de 8 %.
Esa etiqueta era funcion de las mismas 3 variables con las que se entrena el
modelo, que terminaba reconstruyendo la regla (ver docs/technical-debt.md).

Ventana: fecha_observacion es la fecha de corte de las features (columna
fecha_corte de gold_features_cliente); el impago se simula en los
horizonte_meses siguientes. Cada nueva-entrega avanza esa fecha y regenera las
etiquetas. Los meses que llegan despues no reflejan los choques simulados.

Uso:
    python generate_labels.py --gold data/gold/kpis.duckdb \
        --config config/etiquetas_impago.yaml \
        --auditoria-out data/auditoria/etiquetas_latentes.parquet
"""

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.gold.etiquetas_gold import (  # noqa: E402
    RUTA_AUDITORIA,
    escribir_auditoria,
    escribir_etiquetas,
    verificar_esquema_gold,
)
from src.gold.etiquetas_impago import cargar_config, generar_etiquetas  # noqa: E402
from src.gold.risk_features import cargar_features  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def fecha_observacion_de(features: pd.DataFrame) -> dt.date:
    """La fecha de corte con la que se construyeron las features. Debe ser una
    sola para toda la tabla: las etiquetas se observan en una fecha, no varias."""
    if "fecha_corte" not in features.columns:
        raise RuntimeError(
            "gold_features_cliente no tiene la columna fecha_corte: se escribio con "
            "una version anterior de build_features.py. Reconstruyela con "
            "'python main.py features' y vuelve a correr este paso."
        )
    fechas = pd.to_datetime(features["fecha_corte"]).dt.date.unique()
    if len(fechas) != 1:
        raise RuntimeError(
            f"gold_features_cliente tiene {len(fechas)} fechas de corte distintas; "
            "se esperaba una sola."
        )
    return fechas[0]


def generar_y_guardar(gold: str, config: str, auditoria_out: str):
    cfg = cargar_config(config)
    features = cargar_features(gold)
    if features.empty:
        raise RuntimeError(f"gold_features_cliente esta vacia en {gold}")
    fecha_observacion = fecha_observacion_de(features)

    tabla, auditoria, resultado = generar_etiquetas(features, cfg, fecha_observacion)

    escribir_etiquetas(tabla, gold)
    escribir_auditoria(auditoria, auditoria_out)
    verificar_esquema_gold(gold)

    n = len(tabla)
    positivos = int(tabla["impago_posterior"].sum())
    logger.info(
        "Etiquetas: %d clientes, %d con impago en los %d meses posteriores a %s "
        "(%.1f%%; tasa objetivo %.1f%%, semilla %d, b0 calibrado %.3f)",
        n,
        positivos,
        cfg.horizonte_meses,
        fecha_observacion,
        100 * positivos / n,
        100 * cfg.tasa_objetivo,
        cfg.semilla,
        resultado.b0,
    )
    return tabla, resultado


def main():
    parser = argparse.ArgumentParser(
        description="Genera la etiqueta de impago como evento posterior (modelo latente)"
    )
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument("--config", default="config/etiquetas_impago.yaml")
    parser.add_argument("--auditoria-out", default=RUTA_AUDITORIA)
    args = parser.parse_args()

    try:
        generar_y_guardar(args.gold, args.config, args.auditoria_out)
    except Exception:
        logger.exception("Fallo la generacion de etiquetas")
        raise


if __name__ == "__main__":
    main()

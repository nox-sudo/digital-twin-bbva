"""
generate_labels.py

Genera etiquetas sinteticas de riesgo de impago por cliente, para
entrenar el modelo de clasificacion. No existen etiquetas reales: es
un proyecto con datos sinteticos, sin historial de impago observado.

Regla de negocio: un cliente se marca de riesgo alto (label=1) si
cumple al menos 2 de estas 3 condiciones, evaluadas contra el
percentil 75/25 de la poblacion de la corrida actual:
  - ratio_endeudamiento en el cuartil superior (deuda alta vs ingreso)
  - capacidad_ahorro en el cuartil inferior (ahorra poco o gasta de mas)
  - uso_linea_credito en el cuartil superior (usa casi todo su limite)

Nota de diseno: la regla pedida originalmente exigia las 3 condiciones
a la vez (AND estricto). Se probo contra los datos reales y esa regla
deja solo 6 clientes de riesgo sobre 500 (1.2 por ciento), insuficiente
para un split 80/20 confiable (el conjunto de prueba quedaria con 0 o 1
positivo). Se ajusto a "al menos 2 de 3", que da 44 positivos (8.8 por
ciento): suficiente para entrenar y evaluar con un desbalance manejable.

Como ratio_endeudamiento y uso_linea_credito no existen para clientes
sin ese producto (sin tarjeta de credito o prestamo), su ausencia se
interpreta como "no cumple la condicion", no como dato faltante.

Uso:
    python generate_labels.py --silver data/silver \
        --out data/labels/risk_labels.parquet
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.spark_session import get_spark_session  # noqa: E402
from src.gold.kpi_definitions import (  # noqa: E402
    kpi_capacidad_ahorro,
    kpi_ratio_endeudamiento,
    kpi_uso_linea_credito,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def generar_etiquetas(spark, silver_path: str):
    """Calcula los 3 KPIs de riesgo desde Silver y deriva la etiqueta
    0/1 por cliente."""
    entidades = ["clientes", "cuentas", "cetes_inversiones", "transacciones"]
    ctx = {e: spark.read.format("delta").load(f"{silver_path}/{e}") for e in entidades}

    ratio = kpi_ratio_endeudamiento(ctx).rename(
        columns={"valor_numerico": "ratio_endeudamiento"}
    )
    ahorro = kpi_capacidad_ahorro(ctx).rename(
        columns={"valor_numerico": "capacidad_ahorro"}
    )
    uso = kpi_uso_linea_credito(ctx).rename(
        columns={"valor_numerico": "uso_linea_credito"}
    )

    clientes_ids = ctx["clientes"].select("cliente_id").toPandas()

    etiquetas = (
        clientes_ids.merge(ratio, on="cliente_id", how="left")
        .merge(ahorro, on="cliente_id", how="left")
        .merge(uso, on="cliente_id", how="left")
    )

    # Las columnas vienen de PySpark DecimalType via toPandas(), que las
    # deja como objetos decimal.Decimal; quantile() no puede interpolar
    # eso con floats, asi que se castea explicitamente antes de operar.
    columnas_riesgo = ["ratio_endeudamiento", "capacidad_ahorro", "uso_linea_credito"]
    etiquetas[columnas_riesgo] = etiquetas[columnas_riesgo].astype(float)

    p75_ratio = etiquetas["ratio_endeudamiento"].quantile(0.75)
    p25_ahorro = etiquetas["capacidad_ahorro"].quantile(0.25)
    p75_uso = etiquetas["uso_linea_credito"].quantile(0.75)

    logger.info(
        "Umbrales calculados: ratio_endeudamiento > %.3f, capacidad_ahorro < %.2f, "
        "uso_linea_credito > %.3f",
        p75_ratio,
        p25_ahorro,
        p75_uso,
    )

    condiciones_cumplidas = (
        (etiquetas["ratio_endeudamiento"] > p75_ratio).fillna(False).astype(int)
        + (etiquetas["capacidad_ahorro"] < p25_ahorro).fillna(False).astype(int)
        + (etiquetas["uso_linea_credito"] > p75_uso).fillna(False).astype(int)
    )
    etiquetas["label"] = (condiciones_cumplidas >= 2).astype(int)

    return etiquetas[
        [
            "cliente_id",
            "ratio_endeudamiento",
            "capacidad_ahorro",
            "uso_linea_credito",
            "label",
        ]
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Genera etiquetas sinteticas de riesgo de impago"
    )
    parser.add_argument("--silver", default="data/silver")
    parser.add_argument("--out", default="data/labels/risk_labels.parquet")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    spark = get_spark_session("generar_etiquetas_riesgo")
    try:
        etiquetas = generar_etiquetas(spark, args.silver)
        etiquetas.to_parquet(out_path, index=False)

        n_total = len(etiquetas)
        n_positivos = int(etiquetas["label"].sum())
        logger.info(
            "Etiquetas escritas en %s: %d clientes, %d de riesgo alto (%.1f%%)",
            out_path,
            n_total,
            n_positivos,
            100 * n_positivos / n_total,
        )
    except Exception:
        logger.exception("Fallo la generacion de etiquetas")
        raise
    finally:
        spark.stop()


if __name__ == "__main__":
    main()

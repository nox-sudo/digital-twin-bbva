"""
src/gold/risk_features.py

Feature engineering compartido para el modelo de riesgo crediticio.
Arma una tabla de features por cliente combinando los 11 KPIs de Gold
(todos menos probabilidad_impago, que es el objetivo a predecir) con
variables de comportamiento calculadas directo de Silver.

Se centraliza aca y no se duplica entre train_model.py y
predict_risk.py: si las features de entrenamiento y de inferencia se
calcularan por separado, un cambio en una sin la otra desalinearia el
modelo silenciosamente (train/serve skew).
"""

import logging

import duckdb
import pandas as pd
from pyspark.sql import functions as F

logger = logging.getLogger(__name__)

KPIS_NUMERICOS = [
    "ingreso_mensual_promedio",
    "estabilidad_ingreso",
    "gasto_mensual_total",
    "gasto_promedio_3_meses",
    "capacidad_ahorro",
    "saldo_liquido_disponible",
    "ratio_endeudamiento",
    "uso_linea_credito",
    "frecuencia_transacciones",
    "actividad_p2p",
]
KPI_CATEGORICO = "categoria_gasto_dominante"

# Ausencia de estos dos KPIs significa que el cliente no tiene ese
# producto (tarjeta de credito o prestamo), no un dato faltante.
KPIS_CON_CERO_POR_DEFECTO = ["ratio_endeudamiento", "uso_linea_credito"]


def _pivotear_kpis_gold(gold_duckdb_path: str) -> pd.DataFrame:
    """Lee gold_kpis (formato largo) y lo pivotea a una fila por
    cliente_id, una columna por KPI."""
    con = duckdb.connect(gold_duckdb_path, read_only=True)
    try:
        largo = con.execute(
            "SELECT cliente_id, kpi_id, valor_numerico, valor_texto FROM gold_kpis "
            "WHERE kpi_id != 'probabilidad_impago'"
        ).fetchdf()
    finally:
        con.close()

    numericos = largo[largo["kpi_id"].isin(KPIS_NUMERICOS)]
    ancho = numericos.pivot(
        index="cliente_id", columns="kpi_id", values="valor_numerico"
    )
    ancho = ancho.reindex(columns=KPIS_NUMERICOS)

    categorico = largo[largo["kpi_id"] == KPI_CATEGORICO][["cliente_id", "valor_texto"]]
    categorico = categorico.rename(columns={"valor_texto": KPI_CATEGORICO}).set_index(
        "cliente_id"
    )
    ancho = ancho.join(categorico, how="left")

    ancho[KPIS_CON_CERO_POR_DEFECTO] = ancho[KPIS_CON_CERO_POR_DEFECTO].fillna(0.0)
    return ancho


def _variables_comportamiento_silver(spark, silver_path: str) -> pd.DataFrame:
    """Variables de comportamiento que no viven en el catalogo de
    KPIs de Gold: edad, antiguedad como cliente, ingreso declarado,
    numero de productos, y proporcion de retiros en efectivo."""
    clientes = spark.read.format("delta").load(f"{silver_path}/clientes")
    cuentas = spark.read.format("delta").load(f"{silver_path}/cuentas")
    transacciones = spark.read.format("delta").load(f"{silver_path}/transacciones")

    demograficas = clientes.select(
        "cliente_id",
        (F.datediff(F.current_date(), F.col("fecha_nacimiento")) / 365.25).alias(
            "edad_anios"
        ),
        F.datediff(F.current_date(), F.col("fecha_alta")).alias("antiguedad_dias"),
        F.col("ingreso_mensual_declarado").cast("double").alias("ingreso_declarado"),
    )

    productos = cuentas.groupBy("cliente_id").agg(
        F.countDistinct("tipo_cuenta").alias("numero_productos"),
        F.max((F.col("tipo_cuenta") == "tarjeta_credito").cast("int")).alias(
            "tiene_tarjeta_credito"
        ),
        F.max((F.col("tipo_cuenta") == "prestamo_personal").cast("int")).alias(
            "tiene_prestamo_personal"
        ),
    )

    conteos_tx = transacciones.groupBy("cliente_id").agg(
        F.count("transaccion_id").alias("total_transacciones"),
        F.sum((F.col("tipo_transaccion") == "retiro").cast("int")).alias(
            "total_retiros"
        ),
    )
    comportamiento_tx = conteos_tx.withColumn(
        "proporcion_retiros", F.col("total_retiros") / F.col("total_transacciones")
    ).select("cliente_id", "proporcion_retiros")

    resultado = demograficas.join(productos, on="cliente_id", how="left").join(
        comportamiento_tx, on="cliente_id", how="left"
    )
    return resultado.toPandas().set_index("cliente_id")


def construir_features(spark, silver_path: str, gold_duckdb_path: str) -> pd.DataFrame:
    """Arma la tabla de features por cliente: los 11 KPIs de Gold
    (todos menos probabilidad_impago) mas variables de comportamiento
    de Silver. Devuelve un DataFrame de pandas indexado por
    cliente_id, con categoria_gasto_dominante codificada con one-hot.

    Se usa identica en train_model.py y predict_risk.py para que las
    features de entrenamiento e inferencia nunca se desalineen.
    """
    logger.info("Armando KPIs de Gold desde %s", gold_duckdb_path)
    kpis = _pivotear_kpis_gold(gold_duckdb_path)

    logger.info("Armando variables de comportamiento desde Silver (%s)", silver_path)
    comportamiento = _variables_comportamiento_silver(spark, silver_path)

    features = kpis.join(comportamiento, how="left")
    features = pd.get_dummies(
        features, columns=[KPI_CATEGORICO], prefix="categoria_dominante", dummy_na=False
    )

    columnas_comportamiento = [
        "numero_productos",
        "tiene_tarjeta_credito",
        "tiene_prestamo_personal",
        "proporcion_retiros",
    ]
    features[columnas_comportamiento] = features[columnas_comportamiento].fillna(0.0)

    logger.info("Features listas: %d filas, %d columnas", *features.shape)
    return features

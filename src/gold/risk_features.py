"""
src/gold/risk_features.py

Feature store ligero del proyecto: una tabla Gold con una fila por
cliente (gold_features_cliente, en el mismo DuckDB que gold_kpis), que
combina los 11 KPIs de Gold (todos menos probabilidad_impago, que es el
objetivo a predecir) con variables de comportamiento calculadas desde
Silver.

Por que persistirla y no recalcularla en cada consumidor:
- Train/serve skew: train_model.py y predict_risk.py leen exactamente
  la misma tabla, no dos calculos que podrian desalinearse.
- Reuso: dashboard, simulador Monte Carlo y asistente RAG necesitan el
  perfil del cliente (edad, productos, antiguedad) sin levantar Spark
  ni duplicar esta logica. Para el RAG ademas importa la forma: una
  tabla ancha con columnas con nombre es mucho mas facil de consultar
  con text-to-SQL que gold_kpis en formato largo.

Por eso la tabla guarda la version legible (categoria_gasto_dominante
como texto). La codificacion one-hot que necesita XGBoost se aplica al
momento de entrenar o predecir, en preparar_para_modelo().

Flujo:
    build_features.py   construir_features() -> escribir_features()
    train_model.py      cargar_features() -> preparar_para_modelo()
    predict_risk.py     cargar_features() -> preparar_para_modelo(columnas del modelo)
"""

import logging
from datetime import datetime

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

TABLA_FEATURES = "gold_features_cliente"

# Columnas de la tabla que describen la fila, no al cliente: se excluyen
# al preparar la matriz para el modelo.
COLUMNAS_METADATA = ["fecha_calculo"]

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
    # Import local: solo build_features.py necesita Spark. Asi train_model.py
    # y predict_risk.py pueden importar este modulo sin cargar PySpark.
    from pyspark.sql import functions as F

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
    de Silver. Devuelve un DataFrame de pandas indexado por cliente_id,
    en forma legible (sin one-hot), listo para escribir_features().
    """
    logger.info("Armando KPIs de Gold desde %s", gold_duckdb_path)
    kpis = _pivotear_kpis_gold(gold_duckdb_path)

    logger.info("Armando variables de comportamiento desde Silver (%s)", silver_path)
    comportamiento = _variables_comportamiento_silver(spark, silver_path)

    features = kpis.join(comportamiento, how="left")

    columnas_comportamiento = [
        "numero_productos",
        "tiene_tarjeta_credito",
        "tiene_prestamo_personal",
        "proporcion_retiros",
    ]
    features[columnas_comportamiento] = features[columnas_comportamiento].fillna(0.0)

    logger.info("Features listas: %d filas, %d columnas", *features.shape)
    return features


def escribir_features(features: pd.DataFrame, gold_duckdb_path: str) -> int:
    """Persiste la tabla de features en el DuckDB de Gold, reemplazando
    la version anterior completa (snapshot por corrida, igual que
    gold_kpis). Devuelve el numero de filas escritas."""
    tabla = features.reset_index()
    tabla["fecha_calculo"] = datetime.now()

    con = duckdb.connect(gold_duckdb_path)
    try:
        con.register("features_df", tabla)
        con.execute(
            f"CREATE OR REPLACE TABLE {TABLA_FEATURES} AS SELECT * FROM features_df"
        )
        n_filas = con.execute(f"SELECT COUNT(*) FROM {TABLA_FEATURES}").fetchone()[0]
    finally:
        con.close()

    logger.info(
        "%s escrita: %d clientes, %d columnas", TABLA_FEATURES, n_filas, tabla.shape[1]
    )
    return n_filas


def cargar_features(gold_duckdb_path: str) -> pd.DataFrame:
    """Lee la tabla de features desde Gold, indexada por cliente_id."""
    con = duckdb.connect(gold_duckdb_path, read_only=True)
    try:
        features = con.execute(f"SELECT * FROM {TABLA_FEATURES}").fetchdf()
    except duckdb.CatalogException as error:
        raise RuntimeError(
            f"No existe la tabla {TABLA_FEATURES} en {gold_duckdb_path}. "
            "Corre primero build_features.py (python main.py features)."
        ) from error
    finally:
        con.close()
    return features.set_index("cliente_id")


def preparar_para_modelo(
    features: pd.DataFrame, columnas_modelo: list[str] | None = None
) -> pd.DataFrame:
    """Convierte la tabla legible en la matriz numerica que recibe el
    modelo: quita metadata y codifica categoria_gasto_dominante con
    one-hot.

    En entrenamiento (columnas_modelo=None) las columnas salen de los
    datos. En prediccion se pasan las columnas con las que se entreno el
    modelo (modelo.feature_names_in_): si en los datos nuevos aparece
    una categoria que el modelo no vio, se descarta; si falta una que si
    vio, se rellena con 0. Sin esto, el modelo falla o, peor, recibe
    columnas en otro orden.
    """
    matriz = features.drop(columns=COLUMNAS_METADATA, errors="ignore")
    matriz = pd.get_dummies(
        matriz, columns=[KPI_CATEGORICO], prefix="categoria_dominante", dummy_na=False
    )
    if columnas_modelo is not None:
        matriz = matriz.reindex(columns=list(columnas_modelo), fill_value=0)
    return matriz

"""
src/gold/movimientos.py

Tabla gold_movimientos_mensuales: una fila por cliente, mes, tipo de
movimiento y categoria, con el monto total y el numero de movimientos.

Por que existe: gold_kpis resume a cada cliente en un solo numero por
KPI (un promedio de 12 meses), y eso no alcanza para tres consumidores
que necesitan la serie mes a mes:

  - el dashboard, para mostrar el flujo de efectivo de un cliente y en
    que categorias gasta;
  - el simulador Monte Carlo, que muestrea ingresos y gastos mensuales
    historicos en vez de suponer una distribucion;
  - el asistente RAG, para preguntas como "cuanto gaste en comida en
    marzo".

Sin esta tabla, cada uno tendria que leer Silver (Delta, con Spark) por
su cuenta. Con ella, los tres leen la misma agregacion desde DuckDB.

Grano: (cliente_id, mes, tipo_transaccion, categoria). El monto conserva
el signo de Silver: entradas positivas (nomina, p2p_recibido) y salidas
negativas (compra, pago_recurrente, retiro, p2p_enviado), asi que el
flujo neto de un mes es la suma simple de monto_total.
"""

import pandas as pd
from pyspark.sql import DataFrame
from pyspark.sql import functions as F

TABLA_MOVIMIENTOS = "gold_movimientos_mensuales"


def movimientos_mensuales(transacciones: DataFrame) -> pd.DataFrame:
    """Agrega las transacciones de Silver al grano de la tabla."""
    agregado = (
        transacciones.withColumn("mes", F.date_format("fecha", "yyyy-MM"))
        .groupBy("cliente_id", "mes", "tipo_transaccion", "categoria")
        .agg(
            # double, no decimal: DuckDB y pandas lo manejan sin conversiones,
            # y a esta escala (centavos) la precision sobra.
            F.sum("monto").cast("double").alias("monto_total"),
            F.count("*").alias("n_movimientos"),
        )
        .orderBy("cliente_id", "mes", "tipo_transaccion", "categoria")
    )
    return agregado.toPandas()

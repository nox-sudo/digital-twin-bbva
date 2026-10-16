"""
src/gold/kpi_definitions.py

Logica de calculo de cada uno de los 12 KPIs del catalogo. A
diferencia de las reglas de negocio de Silver, un calculo de KPI no
se puede expresar de forma puramente declarativa (son agregaciones,
joins, y ventanas de tiempo distintas entre si) - por eso cada KPI es
una funcion pequeña, no una entrada de configuracion.

Cada funcion recibe el silver_context (diccionario de entidad ->
DataFrame de PySpark) y devuelve un DataFrame de pandas con las
columnas: cliente_id, valor_numerico (o valor_texto para KPIs de
categoria).

Se opera sobre datos ya limpios y validados de Silver - ningun KPI se
calcula directamente sobre Bronze.
"""

from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Tipos de movimiento que cuentan como ingreso: nomina (empleo formal) y
# deposito (ingreso informal: efectivo o transferencias de clientes). Las
# transferencias P2P no cuentan: son dinero entre personas, no ingreso.
TIPOS_INGRESO = ["nomina", "deposito"]
# Salidas que reducen la capacidad de ahorro: consumo, pago de deudas y
# efectivo. Los envios P2P quedan fuera, igual que los recibidos.
TIPOS_SALIDA = ["compra", "pago_recurrente", "retiro"]


def _serie_mensual(ctx, tipos: list[str], desde=None):
    """Monto mensual por cliente sobre la rejilla completa cliente x mes.

    Un mes sin movimientos de esos tipos vale 0, no desaparece. Promediar
    solo los meses con movimientos inflaba el ingreso de quien no cobra
    todos los meses (un cliente que cobra 5 de 12 meses aparecia con el
    ingreso de un mes bueno) y ocultaba esa irregularidad en la
    desviacion estandar. Devuelve cliente_id, mes, monto (valor absoluto).
    """
    tx = ctx["transacciones"].withColumn("mes", F.date_format("fecha", "yyyy-MM"))
    if desde is not None:
        tx = tx.filter(F.col("fecha") >= desde)
    meses = tx.select("mes").distinct()
    rejilla = ctx["clientes"].select("cliente_id").crossJoin(meses)
    sumas = (
        tx.filter(F.col("tipo_transaccion").isin(tipos))
        .groupBy("cliente_id", "mes")
        .agg(F.sum(F.abs("monto")).alias("monto"))
    )
    return rejilla.join(sumas, ["cliente_id", "mes"], "left").fillna(0, ["monto"])


def _promedio_mensual(ctx, tipos: list[str], desde=None):
    return (
        _serie_mensual(ctx, tipos, desde)
        .groupBy("cliente_id")
        .agg(F.avg("monto").cast("double").alias("valor_numerico"))
        .toPandas()
    )


def kpi_ingreso_mensual_promedio(ctx):
    return _promedio_mensual(ctx, TIPOS_INGRESO)


def kpi_estabilidad_ingreso(ctx):
    resultado = (
        _serie_mensual(ctx, TIPOS_INGRESO)
        .groupBy("cliente_id")
        .agg(F.stddev_samp("monto").cast("double").alias("valor_numerico"))
    )
    return resultado.toPandas()


def kpi_gasto_mensual_total(ctx):
    return _promedio_mensual(ctx, ["compra"])


def kpi_gasto_promedio_3_meses(ctx):
    tx = ctx["transacciones"]
    fecha_max = tx.agg(F.max("fecha")).collect()[0][0]
    fecha_corte = fecha_max.replace(day=1)
    # Aproximacion de 3 meses hacia atras desde el mes mas reciente
    for _ in range(2):
        mes = fecha_corte.month - 1 or 12
        anio = fecha_corte.year - (1 if fecha_corte.month == 1 else 0)
        fecha_corte = fecha_corte.replace(year=anio, month=mes)
    return _promedio_mensual(ctx, ["compra"], desde=fecha_corte)


def kpi_categoria_gasto_dominante(ctx):
    tx = ctx["transacciones"]
    compras = tx.filter(F.col("tipo_transaccion") == "compra")
    por_categoria = compras.groupBy("cliente_id", "categoria").agg(
        F.sum(F.abs("monto")).alias("total_categoria")
    )
    window_rank = (
        por_categoria.withColumn(
            "rango",
            F.row_number().over(
                Window.partitionBy("cliente_id").orderBy(
                    F.col("total_categoria").desc()
                )
            ),
        )
        .filter(F.col("rango") == 1)
        .select("cliente_id", F.col("categoria").alias("valor_texto"))
    )
    return window_rank.toPandas()


def kpi_capacidad_ahorro(ctx):
    """Ingreso mensual promedio menos salidas mensuales promedio (compras,
    pagos de deuda y retiros). Antes restaba solo compras, asi que un
    cliente con un prestamo grande aparecia con buena capacidad de ahorro."""
    ingreso = kpi_ingreso_mensual_promedio(ctx).rename(
        columns={"valor_numerico": "ingreso"}
    )
    salidas = _promedio_mensual(ctx, TIPOS_SALIDA).rename(
        columns={"valor_numerico": "salidas"}
    )
    combinado = ingreso.merge(salidas, on="cliente_id", how="outer").fillna(0)
    combinado["valor_numerico"] = combinado["ingreso"] - combinado["salidas"]
    return combinado[["cliente_id", "valor_numerico"]]


def kpi_saldo_liquido_disponible(ctx):
    cuentas = ctx["cuentas"]
    digitales = cuentas.filter(F.col("tipo_cuenta") == "cuenta_digital")
    resultado = digitales.select(
        "cliente_id", F.col("saldo_actual").alias("valor_numerico")
    )
    return resultado.toPandas()


def kpi_ratio_endeudamiento(ctx):
    cuentas = ctx["cuentas"]
    deuda = (
        cuentas.filter(
            F.col("tipo_cuenta").isin("tarjeta_credito", "prestamo_personal")
        )
        .groupBy("cliente_id")
        .agg(F.sum("saldo_actual").cast("double").alias("deuda_total"))
    )
    ingreso = kpi_ingreso_mensual_promedio(ctx).rename(
        columns={"valor_numerico": "ingreso"}
    )

    deuda_pd = deuda.toPandas()
    combinado = deuda_pd.merge(ingreso, on="cliente_id", how="left")
    combinado = combinado[combinado["ingreso"].notna() & (combinado["ingreso"] != 0)]
    combinado["valor_numerico"] = combinado["deuda_total"] / combinado["ingreso"]
    return combinado[["cliente_id", "valor_numerico"]]


def kpi_uso_linea_credito(ctx):
    cuentas = ctx["cuentas"]
    tarjetas = cuentas.filter(F.col("tipo_cuenta") == "tarjeta_credito")
    tarjetas = tarjetas.filter(
        F.col("limite_credito").isNotNull() & (F.col("limite_credito") > 0)
    )
    resultado = tarjetas.select(
        "cliente_id",
        (F.col("saldo_actual") / F.col("limite_credito")).alias("valor_numerico"),
    )
    return resultado.toPandas()


def kpi_probabilidad_impago(ctx):
    """
    Se escribe con valor NULL para todos los clientes: el valor real lo
    calcula el modelo de riesgo despues de Gold (predict_risk.py
    reemplaza estas filas). Asi gold_kpis tiene el catalogo completo en
    estructura aun antes de que corra el modelo.
    """
    clientes = ctx["clientes"]
    resultado = clientes.select("cliente_id").withColumn(
        "valor_numerico", F.lit(None).cast("double")
    )
    return resultado.toPandas()


def kpi_frecuencia_transacciones(ctx):
    tx = ctx["transacciones"]
    por_mes = (
        tx.withColumn("mes", F.date_format("fecha", "yyyy-MM"))
        .groupBy("cliente_id", "mes")
        .agg(F.count("transaccion_id").alias("conteo_mes"))
    )
    resultado = por_mes.groupBy("cliente_id").agg(
        F.avg("conteo_mes").alias("valor_numerico")
    )
    return resultado.toPandas()


def kpi_actividad_p2p(ctx):
    """Neto mensual P2P (recibido menos enviado), promediado sobre todos los
    meses: un mes sin transferencias cuenta como 0."""
    recibido = _serie_mensual(ctx, ["p2p_recibido"]).withColumnRenamed("monto", "rec")
    enviado = _serie_mensual(ctx, ["p2p_enviado"]).withColumnRenamed("monto", "env")
    resultado = (
        recibido.join(enviado, ["cliente_id", "mes"])
        .groupBy("cliente_id")
        .agg(F.avg(F.col("rec") - F.col("env")).cast("double").alias("valor_numerico"))
    )
    return resultado.toPandas()


# Registro central: kpi_id -> funcion de calculo.
# Agregar un KPI nuevo significa escribir la funcion y agregar una
# linea aqui, mas su metadata en config/kpi_catalog.yaml.
KPI_REGISTRY = {
    "ingreso_mensual_promedio": kpi_ingreso_mensual_promedio,
    "estabilidad_ingreso": kpi_estabilidad_ingreso,
    "gasto_mensual_total": kpi_gasto_mensual_total,
    "gasto_promedio_3_meses": kpi_gasto_promedio_3_meses,
    "categoria_gasto_dominante": kpi_categoria_gasto_dominante,
    "capacidad_ahorro": kpi_capacidad_ahorro,
    "saldo_liquido_disponible": kpi_saldo_liquido_disponible,
    "ratio_endeudamiento": kpi_ratio_endeudamiento,
    "uso_linea_credito": kpi_uso_linea_credito,
    "probabilidad_impago": kpi_probabilidad_impago,
    "frecuencia_transacciones": kpi_frecuencia_transacciones,
    "actividad_p2p": kpi_actividad_p2p,
}

"""
dashboard/kpis.py

Diccionario unico de nombres y descripciones de los KPIs que muestra el
dashboard, para que el mismo KPI se llame igual en todas las vistas.

Cada KPI tiene dos nombres, uno por rol:

  - admin: el termino de negocio ("Capacidad de ahorro").
  - cliente: lo mismo en segunda persona y sin jerga ("Te queda al mes").

y una descripcion de una frase, en lenguaje sencillo, que sale en el
tooltip (help) de la tarjeta. Las descripciones pueden llevar {ventana}
(meses de la ventana de features, config/features.yaml) y
{tasa_objetivo}, que llena ui.ayuda().

Las claves son las columnas de Gold cuando el KPI existe ahi
(config/kpi_catalog.yaml); las demas son cifras que arma el dashboard.
Este archivo no cambia ningun calculo: solo como se llaman las cifras.
Las respuestas del asistente las redacta el modelo con los nombres de sus
herramientas, que no se tocan desde aqui.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Kpi:
    admin: str
    cliente: str
    descripcion: str


KPIS = {
    # --- Cliente ---------------------------------------------------------
    "ingreso_mensual_promedio": Kpi(
        "Ingreso mensual",
        "Te entra al mes",
        "Promedio de lo que entra cada mes por nómina y depósitos en los "
        "últimos {ventana} meses.",
    ),
    "gasto_mensual_total": Kpi(
        "Gasto mensual",
        "Gastas al mes",
        "Promedio de lo que sale cada mes en compras, pagos de deuda y "
        "retiros en los últimos {ventana} meses.",
    ),
    "capacidad_ahorro": Kpi(
        "Capacidad de ahorro",
        "Te queda al mes",
        "Ingreso menos compras, pagos de deuda y retiros, en promedio mensual "
        "de los últimos {ventana} meses. Si es negativa, sale más de lo que entra.",
    ),
    "saldo_liquido_disponible": Kpi(
        "Saldo disponible",
        "Tienes disponible",
        "Dinero en la cuenta digital al día de corte, sin contar inversiones.",
    ),
    "probabilidad_impago": Kpi(
        "Probabilidad de impago",
        "Riesgo de atrasarte en tus pagos",
        "Estimación de qué tan probable es atrasarse en un pago en los próximos "
        "meses. Sirve para ordenar el riesgo, no es una certeza.",
    ),
    "banda_riesgo": Kpi(
        "Banda de riesgo",
        "Tu nivel de riesgo",
        "Bajo, medio o alto según la probabilidad de impago.",
    ),
    "ratio_endeudamiento": Kpi(
        "Deuda / ingreso",
        "Lo que debes contra lo que ganas",
        "Saldo de tarjeta y préstamo entre el ingreso de un mes. 0 si no tiene "
        "crédito; sin valor si tiene deuda y no tiene ingreso.",
    ),
    "uso_linea_credito": Kpi(
        "Uso de tarjeta",
        "Usas de tu tarjeta",
        "Parte del límite de la tarjeta de crédito que está usada.",
    ),
    "pago_comprometido": Kpi(
        "Pago de deudas al mes",
        "Pagas de deudas al mes",
        "Pago mínimo de las tarjetas y cuota de los préstamos, con IVA.",
    ),
    "edad_anios": Kpi("Edad", "Tu edad", "Edad al día de corte."),
    "antiguedad_anios": Kpi(
        "Antigüedad", "Años con nosotros", "Tiempo desde que abrió su primera cuenta."
    ),
    "numero_productos": Kpi(
        "Productos",
        "Tus productos",
        "Cuenta digital, tarjeta de crédito y préstamo personal que tiene.",
    ),
    # --- Variables del modelo (monitoreo de deriva) ------------------------
    "estabilidad_ingreso": Kpi(
        "Estabilidad del ingreso",
        "Qué tan parejo es tu ingreso",
        "Cuánto varía el ingreso de un mes a otro: entre más alto, menos parejo.",
    ),
    "gasto_promedio_3_meses": Kpi(
        "Gasto de los últimos 3 meses",
        "Gastas al mes (últimos 3 meses)",
        "Promedio mensual de lo que salió en los últimos 3 meses.",
    ),
    "frecuencia_transacciones": Kpi(
        "Movimientos al mes",
        "Movimientos al mes",
        "Número promedio de movimientos en la cuenta cada mes.",
    ),
    "actividad_p2p": Kpi(
        "Transferencias entre personas",
        "Transferencias a personas",
        "Transferencias enviadas y recibidas con otras personas cada mes.",
    ),
    "ingreso_declarado": Kpi(
        "Ingreso declarado",
        "Ingreso que declaraste",
        "Ingreso mensual que la persona declaró al abrir su cuenta.",
    ),
    "proporcion_retiros": Kpi(
        "Retiros en efectivo",
        "Retiros en efectivo",
        "Parte de lo que sale de la cuenta que se retira en efectivo.",
    ),
    "antiguedad_dias": Kpi(
        "Antigüedad",
        "Tiempo con nosotros",
        "Tiempo desde que abrió su primera cuenta.",
    ),
    "tiene_tarjeta_credito": Kpi(
        "Tiene tarjeta de crédito",
        "Tienes tarjeta de crédito",
        "Si tiene una tarjeta de crédito activa.",
    ),
    "tiene_prestamo_personal": Kpi(
        "Tiene préstamo personal",
        "Tienes préstamo personal",
        "Si tiene un préstamo personal activo.",
    ),
    # --- Portafolio ------------------------------------------------------
    "clientes": Kpi("Clientes", "Clientes", "Clientes con datos en el último corte."),
    "ingreso_mediano": Kpi(
        "Ingreso mensual mediano",
        "Ingreso mensual mediano",
        "La mitad de los clientes gana más y la mitad menos. Ingreso de nómina "
        "y depósitos, promediado en los últimos {ventana} meses.",
    ),
    "pct_ahorro_negativo": Kpi(
        "Gastan más de lo que ganan",
        "Gastan más de lo que ganan",
        "Clientes cuyas compras, pagos de deuda y retiros superan en promedio a "
        "su ingreso en los últimos {ventana} meses.",
    ),
    "probabilidad_media": Kpi(
        "Probabilidad media de impago",
        "Probabilidad media de impago",
        "Promedio de la probabilidad de impago de todos los clientes con score. "
        "Se queda cerca de {tasa_objetivo}, la tasa a la que se calibra el "
        "modelo, así que no mide el deterioro entre cortes.",
    ),
    # --- Simulador de hipoteca ---------------------------------------------
    "cuota_mensual": Kpi(
        "Cuota mensual",
        "Pagarías al mes",
        "Pago mensual de la hipoteca con esta tasa, plazo y enganche.",
    ),
    "riesgo_sin_hipoteca": Kpi(
        "Riesgo sin hipoteca",
        "Riesgo sin hipoteca",
        "De cada 100 escenarios simulados, en cuántos el saldo queda negativo "
        "algún mes solo con el flujo actual.",
    ),
    "riesgo_con_hipoteca": Kpi(
        "Riesgo con hipoteca",
        "Riesgo con hipoteca",
        "La misma simulación, sumando la cuota (menos la renta que deja de pagar).",
    ),
    "capacidad_hipotecaria": Kpi(
        "Capacidad hipotecaria",
        "Casa más cara que te alcanza",
        "Precio máximo de vivienda con el que la hipoteca no sube el riesgo más "
        "de lo permitido, con este enganche, plazo y tasa.",
    ),
}

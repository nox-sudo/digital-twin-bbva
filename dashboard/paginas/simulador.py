"""
Simulador de escenarios: que pasaria con el saldo de un cliente si
contrata una hipoteca. Usa el modelo Monte Carlo de
src/simulacion/montecarlo.py sobre el historial mensual del cliente en
Gold, con los supuestos de config/simulacion.yaml como valores de partida.
"""

import altair as alt
import pandas as pd
import streamlit as st
import yaml

from dashboard import acceso, comun
from src.common.mercado import cargar_mercado, precio_minimo_vivienda
from src.simulacion import montecarlo as mc

st.title("Simulador de hipoteca")
acceso.requiere_rol("admin", "cliente")


@st.cache_data
def config_simulacion() -> dict:
    with open(comun.RAIZ / "config" / "simulacion.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@st.cache_data(show_spinner="Simulando escenarios...")
def correr(
    version: float,
    cliente_id: str,
    precio: float,
    enganche_pct: float,
    plazo_anios: int,
    tasa_anual: float,
    renta: float,
) -> dict:
    """Corre los tres calculos sobre las MISMAS muestras (sin hipoteca, con
    hipoteca y capacidad maxima), para que la diferencia entre escenarios
    sea solo la hipoteca. Se guarda en cache por combinacion de entradas."""
    cfg = config_simulacion()
    gold = comun.config()["gold_duckdb"]
    choque = cfg["choque_ingreso"]
    supuestos = mc.Supuestos(
        meses=int(cfg["simulacion"]["meses"]),
        trayectorias=int(cfg["simulacion"]["trayectorias"]),
        semilla=int(cfg["simulacion"]["semilla"]),
        prob_choque_mensual=float(choque["probabilidad_mensual"]),
        duracion_choque=tuple(int(x) for x in choque["duracion_meses"]),
    )
    hipoteca = mc.Hipoteca(
        precio=precio,
        tasa_anual=tasa_anual,
        plazo_anios=plazo_anios,
        enganche_pct=enganche_pct,
        gastos_iniciales_pct=float(cfg["hipoteca"]["gastos_iniciales_pct"]),
        renta_reemplazada=renta,
    )
    historial = mc.historial_cliente(gold, cliente_id)
    saldo = mc.saldo_inicial_cliente(gold, cliente_id)
    muestras = mc.muestrear(len(historial.meses), supuestos)
    sin = mc.resumir(mc.simular(historial, saldo, muestras))
    con = mc.evaluar_hipoteca(historial, saldo, hipoteca, muestras)
    incremento = float(cfg["incremento_deficit_maximo"])
    return {
        "supuestos": supuestos,
        "hipoteca": hipoteca,
        "saldo": saldo,
        "ingreso_medio": historial.ingreso_medio,
        "ahorro_medio": float(historial.neto.mean()),
        "sin": sin,
        "con": con,
        "capacidad": mc.capacidad_maxima(
            historial,
            saldo,
            hipoteca,
            muestras,
            incremento,
            precio_minimo=precio_minimo_vivienda(),
        ),
        "incremento": incremento,
        "meses_enganche": mc.meses_para_juntar(hipoteca.pago_inicial, historial, saldo),
    }


if comun.gold_disponible():
    version = comun.version_gold()
    clientes = comun.portafolio(version)["cliente_id"].tolist()
    cfg = config_simulacion()["hipoteca"]

    columna_cliente, columna_precio = st.columns([1, 1])
    cliente_id = acceso.cliente_actual(clientes, columna_cliente, "visible")
    precio = columna_precio.number_input(
        "Precio de la vivienda (MXN)", min_value=100_000, value=900_000, step=50_000
    )

    c1, c2, c3, c4 = st.columns(4)
    # Los sliders trabajan en puntos porcentuales para leerse como "10".
    enganche = (
        c1.slider("Enganche (%)", 5, 40, int(round(cfg["enganche_pct"] * 100)), 5) / 100
    )
    plazo = c2.select_slider(
        "Plazo (años)", [5, 10, 15, 20, 25, 30], int(cfg["plazo_anios"])
    )
    tasa_pct = c3.slider(
        "Tasa anual (%)", 6.0, 18.0, float(cfg["tasa_anual"]) * 100, 0.5
    )
    tasa = tasa_pct / 100
    renta = c4.number_input(
        "Renta que deja de pagar (MXN)",
        min_value=0,
        value=0,
        step=500,
        help="Si hoy renta, la vivienda propia elimina ese gasto. El historial no "
        "distingue la renta de otros gastos, así que se indica aquí.",
    )

    # Referencia de mercado junto a la tasa: informativa, no es el CAT de esta
    # simulacion (config/mercado_mx.yaml, con fuente y fecha).
    ref = cargar_mercado()["hipoteca_bbva_fija"]
    st.caption(
        f"Tasa del simulador: {tasa_pct:.2f} % anual fija, sin IVA sobre intereses "
        "(crédito a la vivienda exento, LIVA art. 15, fr. X, inc. d). "
        f"{ref['etiqueta']}: {ref['cat_promedio_sin_iva'] * 100:.1f} %, con tasa de "
        f"{ref['tasa_calculo_cat'] * 100:.2f} %, plazo de {ref['plazo_calculo_anios']} "
        f"años y aforo de {ref['aforo_calculo'] * 100:.0f} %. No es el CAT de este "
        "cliente."
    )

    r = correr(version, cliente_id, float(precio), enganche, plazo, tasa, float(renta))
    hipoteca: mc.Hipoteca = r["hipoteca"]
    sin: mc.Resultado = r["sin"]
    con: mc.Resultado = r["con"]
    meses = r["supuestos"].meses

    # --- Lectura en una frase, para la demo -------------------------------
    st.markdown(
        f"Con esta hipoteca, en **{con.probabilidad_deficit * 100:.0f} de cada 100** "
        f"escenarios simulados el saldo de {cliente_id} queda negativo en algún mes "
        f"de los próximos {meses}. Sin hipoteca: "
        f"**{sin.probabilidad_deficit * 100:.0f} de cada 100**."
    )

    m1, m2, m3, m4 = st.columns(4)
    proporcion = (
        hipoteca.cuota_mensual / r["ingreso_medio"] if r["ingreso_medio"] else None
    )
    m1.metric(
        "Cuota mensual",
        comun.formato_mxn(hipoteca.cuota_mensual),
        help=f"Crédito de {comun.formato_mxn(hipoteca.monto_credito)}. "
        + (
            f"Equivale al {proporcion:.0%} de su ingreso laboral medio."
            if proporcion
            else ""
        ),
    )
    m2.metric(
        "Riesgo sin hipoteca",
        comun.porcentaje(sin.probabilidad_deficit),
        help="Probabilidad de saldo negativo en el horizonte solo con su flujo actual "
        "y el riesgo de perder el ingreso unos meses.",
    )
    m3.metric(
        "Riesgo con hipoteca",
        comun.porcentaje(con.probabilidad_deficit),
        help="Misma simulación, con la cuota (menos la renta que deja de pagar).",
    )
    capacidad = r["capacidad"]
    m4.metric(
        "Capacidad hipotecaria",
        comun.formato_mxn(capacidad.monto) if capacidad.aplica else "No aplica",
        help=(
            f"Precio máximo de vivienda con el que la hipoteca no sube el riesgo más de "
            f"{r['incremento'] * 100:.0f} puntos, con este enganche, plazo y tasa."
            if capacidad.aplica
            else capacidad.detalle
        ),
    )
    if not capacidad.aplica:
        st.caption(
            "Capacidad hipotecaria: no aplica. " + capacidad.detalle.replace("$", "\\$")
        )

    # --- Enganche ------------------------------------------------------------
    meses_enganche = r["meses_enganche"]
    # En st.info y similares, Streamlit interpreta "$...$" como formula; se
    # escapa el signo de pesos.
    pago_inicial = comun.formato_mxn(hipoteca.pago_inicial).replace("$", "\\$")
    ahorro = comun.formato_mxn(r["ahorro_medio"]).replace("$", "\\$")
    if meses_enganche == 0:
        st.success(
            f"Enganche y gastos ({pago_inicial}): los cubre con su saldo actual."
        )
    elif meses_enganche is None:
        st.warning(
            f"Enganche y gastos ({pago_inicial}): con su flujo actual no ahorra, "
            "así que no los reuniría."
        )
    else:
        st.info(
            f"Enganche y gastos ({pago_inicial}): con su ahorro promedio de "
            f"{ahorro} al mes los reuniría en "
            f"{meses_enganche:.0f} meses. La simulación supone que ya los tiene."
        )

    # --- Bandas de saldo -------------------------------------------------------
    st.subheader("Saldo proyectado")
    bandas = pd.concat(
        [
            sin.bandas.assign(escenario="sin hipoteca"),
            con.bandas.assign(escenario="con hipoteca"),
        ]
    )
    escala = alt.Scale(
        domain=["sin hipoteca", "con hipoteca"], range=[comun.AZUL, comun.NARANJA]
    )
    base = alt.Chart(bandas).encode(
        x=alt.X(
            "mes:Q", title="Meses desde la firma", scale=alt.Scale(domain=[0, meses])
        ),
        color=alt.Color(
            "escenario:N", scale=escala, legend=alt.Legend(title=None, orient="top")
        ),
    )
    area = base.mark_area(opacity=0.18).encode(
        y=alt.Y("p10:Q", title="Saldo (MXN)", axis=alt.Axis(format="~s")),
        y2="p90:Q",
    )
    linea = base.mark_line(strokeWidth=2).encode(
        y="p50:Q",
        tooltip=[
            alt.Tooltip("escenario:N", title="Escenario"),
            alt.Tooltip("mes:Q", title="Mes"),
            alt.Tooltip("p10:Q", title="Escenario malo (p10)", format="$,.0f"),
            alt.Tooltip("p50:Q", title="Mediana", format="$,.0f"),
            alt.Tooltip("p90:Q", title="Escenario bueno (p90)", format="$,.0f"),
        ],
    )
    cero = (
        alt.Chart(pd.DataFrame({"y": [0]}))
        .mark_rule(color="#6b6b66", strokeDash=[4, 4])
        .encode(y="y:Q")
    )
    st.altair_chart(
        (area + linea + cero).properties(height=320), use_container_width=True
    )
    st.caption(
        "Línea: mediana de las trayectorias. Banda: del escenario malo (percentil 10) al "
        f"bueno (percentil 90), sobre {r['supuestos'].trayectorias:,} trayectorias. "
        "Debajo de la línea punteada, el saldo no alcanza."
    )

    saldo_md = comun.formato_mxn(r["saldo"]).replace("$", "\\$")
    with st.expander("Cómo se calcula y qué supone"):
        choque = config_simulacion()["choque_ingreso"]
        st.markdown(f"""
- Cada trayectoria arma los próximos {meses} meses eligiendo al azar meses del
  historial del cliente (ingresos y salidas reales, incluidas las deudas que ya paga).
- Además, cada mes hay {choque['probabilidad_mensual']:.0%} de probabilidad de perder el
  ingreso laboral durante {choque['duracion_meses'][0]} a {choque['duracion_meses'][1]}
  meses: el historial de un año no contiene ese riesgo.
- Saldo inicial: la cuenta digital ({saldo_md}). No cuenta CETES ni
  otros activos.
- Tasa, plazo, enganche y gastos de escrituración son supuestos editables
  (`config/simulacion.yaml`), no condiciones de un banco.
- Los meses se eligen de forma independiente: el modelo no reproduce rachas ni
  estacionalidad.
""")

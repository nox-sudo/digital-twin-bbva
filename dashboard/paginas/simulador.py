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

from dashboard import acceso, comun, ui
from dashboard import vista_cliente as vc
from src.common.mercado import cargar_mercado, precio_minimo_vivienda
from src.simulacion import montecarlo as mc
from src.simulacion.supuestos import cargar_simulacion

st.title("Simulador de hipoteca")
usuario = acceso.requiere_rol("admin", "cliente")


@st.cache_data
def config_simulacion() -> dict:
    return cargar_simulacion(comun.RAIZ / "config" / "simulacion.yaml")


@st.cache_data
def horizonte_modelo() -> int:
    """Meses del horizonte de la etiqueta de impago (config/etiquetas_impago.yaml):
    el riesgo del modelo y el del simulador se comparan en ese plazo."""
    with open(comun.RAIZ / "config" / "etiquetas_impago.yaml", encoding="utf-8") as f:
        return int(yaml.safe_load(f)["horizonte_meses"])


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
    horizonte = horizonte_modelo()
    sin = mc.resumir(mc.simular(historial, saldo, muestras), horizonte)
    con = mc.evaluar_hipoteca(historial, saldo, hipoteca, muestras, horizonte)
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
        "Precio de la vivienda (MXN)",
        min_value=100_000,
        value=900_000,
        step=vc.PASO_PRECIO,
        key="sim_precio",
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
        "Tasa anual (%)", 6.0, 18.0, round(float(cfg["tasa_anual"]) * 100, 2), 0.05
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
    nota_tasa = (
        f"Tasa del simulador: {tasa_pct:.2f} % anual fija, sin IVA sobre intereses "
        "(crédito a la vivienda exento, LIVA art. 15, fr. X, inc. d). "
        f"{ref['etiqueta']}: {ref['cat_promedio_sin_iva'] * 100:.1f} %, con tasa de "
        f"{ref['tasa_calculo_cat'] * 100:.2f} %, plazo de {ref['plazo_calculo_anios']} "
        f"años y aforo de {ref['aforo_calculo'] * 100:.0f} %. No es el CAT de este "
        "cliente."
    )
    # El cliente ve la tasa en una linea; la referencia legal y el CAT bajan a
    # "Cómo se calcula".
    if usuario.rol == "admin":
        st.caption(nota_tasa)
    else:
        st.caption(
            f"Tasa fija de {tasa_pct:.2f} % al año. Si un banco te ofrece otra, "
            "cámbiala arriba."
        )

    r = correr(version, cliente_id, float(precio), enganche, plazo, tasa, float(renta))
    hipoteca: mc.Hipoteca = r["hipoteca"]
    sin: mc.Resultado = r["sin"]
    con: mc.Resultado = r["con"]
    meses = r["supuestos"].meses

    # --- Respuesta para el cliente ----------------------------------------
    # Primero si le alcanza y que cambiar; luego el riesgo y el enganche. El
    # admin conserva la lectura de analista de abajo.
    if usuario.rol != "admin":
        veredicto = vc.veredicto_hipoteca(
            float(precio),
            hipoteca.cuota_mensual,
            r["ingreso_medio"],
            r["capacidad"].monto,
            r["capacidad"].motivo,
            precio_minimo_vivienda(),
            r["ahorro_medio"],
        )
        ui.tarjeta_heroe(veredicto["titulo"], veredicto["valor"], veredicto["detalle"])
        st.markdown(ui.md(veredicto["sugerencia"]))
        if veredicto["probar"]:

            def probar_precio(valor: float) -> None:
                st.session_state["sim_precio"] = int(valor)

            st.button(
                ui.md(f"Probar con {vc.pesos(veredicto['probar'])}"),
                on_click=probar_precio,
                args=(veredicto["probar"],),
            )
        st.info(
            ui.md(
                vc.frase_enganche(
                    hipoteca.pago_inicial, r["meses_enganche"], r["ahorro_medio"]
                )
            )
        )

    # --- Lectura en una frase, para la demo -------------------------------
    # El admin ve el conteo exacto; el cliente, los extremos (0 o 100 de cada
    # 100) en palabras (vista_cliente.certeza).
    if usuario.rol == "admin":
        st.markdown(
            f"Con esta hipoteca, en **{con.probabilidad_deficit * 100:.0f} de cada 100** "
            f"escenarios simulados el saldo de {cliente_id} queda negativo en algún mes "
            f"de los próximos {meses}. Sin hipoteca: "
            f"**{sin.probabilidad_deficit * 100:.0f} de cada 100**."
        )
    else:
        st.markdown(
            vc.frase_riesgo_compra(
                con.probabilidad_deficit, sin.probabilidad_deficit, meses
            ).replace("la compra", "la hipoteca")
        )
    # La comparacion con el horizonte del modelo es para el admin: explica por
    # que las dos cifras de riesgo no coinciden.
    if usuario.rol == "admin":
        st.caption(
            f"En los próximos {horizonte_modelo()} meses, el horizonte del modelo de "
            f"riesgo: {con.probabilidad_deficit_horizonte * 100:.0f} de cada 100 con "
            f"hipoteca y {sin.probabilidad_deficit_horizonte * 100:.0f} sin ella. El "
            "modelo estima la probabilidad de impago en ese plazo; el simulador, la de "
            "quedar en saldo negativo, así que las dos cifras no tienen por qué coincidir."
        )

    m1, m2, m3, m4 = st.columns(4)
    proporcion = (
        hipoteca.cuota_mensual / r["ingreso_medio"] if r["ingreso_medio"] else None
    )
    ui.tarjeta_kpi(
        ui.nombre_kpi("cuota_mensual"),
        ui.formatear_mxn(hipoteca.cuota_mensual),
        ayuda=ui.ayuda(
            "cuota_mensual",
            f"Crédito de {ui.formatear_mxn(hipoteca.monto_credito)}."
            + (
                f" Equivale al {ui.formatear_porcentaje(proporcion)} de su ingreso "
                "laboral medio."
                if proporcion
                else ""
            ),
        ),
        contenedor=m1,
    )
    ui.tarjeta_kpi(
        ui.nombre_kpi("riesgo_sin_hipoteca"),
        ui.formatear_porcentaje(sin.probabilidad_deficit),
        ayuda=ui.ayuda("riesgo_sin_hipoteca"),
        contenedor=m2,
    )
    ui.tarjeta_kpi(
        ui.nombre_kpi("riesgo_con_hipoteca"),
        ui.formatear_porcentaje(con.probabilidad_deficit),
        ayuda=ui.ayuda("riesgo_con_hipoteca"),
        contenedor=m3,
    )
    capacidad = r["capacidad"]
    ui.tarjeta_kpi(
        ui.nombre_kpi("capacidad_hipotecaria"),
        ui.formatear_mxn(capacidad.monto) if capacidad.aplica else "No aplica",
        ayuda=(
            ui.ayuda(
                "capacidad_hipotecaria",
                f"Aquí, que no lo suba más de {r['incremento'] * 100:.0f} puntos.",
            )
            if capacidad.aplica
            else capacidad.detalle
        ),
        contenedor=m4,
    )
    if not capacidad.aplica and usuario.rol == "admin":
        st.caption("Capacidad hipotecaria: no aplica. " + ui.md(capacidad.detalle))

    # --- Enganche (el cliente lo ve arriba, en segunda persona) ---------------
    if usuario.rol == "admin":
        meses_enganche = r["meses_enganche"]
        # En st.info y similares, Streamlit interpreta "$...$" como formula; se
        # escapa el signo de pesos.
        pago_inicial = ui.md(ui.formatear_mxn(hipoteca.pago_inicial))
        ahorro = ui.md(ui.formatear_mxn(r["ahorro_medio"]))
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
    st.subheader("Saldo proyectado" if usuario.rol == "admin" else "Tu saldo mes a mes")
    bandas = pd.concat(
        [
            sin.bandas.assign(escenario="sin hipoteca"),
            con.bandas.assign(escenario="con hipoteca"),
        ]
    )
    for percentil in ("p10", "p50", "p90"):
        bandas[f"{percentil}_texto"] = bandas[percentil].map(ui.formatear_mxn)
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
            alt.Tooltip("p10_texto:N", title="Escenario malo (p10)"),
            alt.Tooltip("p50_texto:N", title="Mediana"),
            alt.Tooltip("p90_texto:N", title="Escenario bueno (p90)"),
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
    if usuario.rol == "admin":
        st.caption(
            "Línea: mediana de las trayectorias. Banda: del escenario malo "
            f"(percentil 10) al bueno (percentil 90), sobre "
            f"{r['supuestos'].trayectorias:,} trayectorias. Debajo de la línea "
            "punteada, el saldo no alcanza."
        )
    else:
        st.caption(
            "La línea es lo más probable; la banda va de una racha mala a una buena. "
            "Debajo de la línea punteada te quedarías sin dinero."
        )

    saldo_md = ui.md(ui.formatear_mxn(r["saldo"]))
    with st.expander("Cómo se calcula y qué supone"):
        choque = config_simulacion()["choque_ingreso"]
        if usuario.rol != "admin":
            st.markdown(f"""
- Simulamos {r['supuestos'].trayectorias:,} versiones de tus próximos {meses} meses,
  armadas con meses reales de tu historial: lo que te entró y lo que salió,
  incluidas las deudas que ya pagas.
- En cada mes hay {choque['probabilidad_mensual']:.0%} de probabilidad de que
  pierdas tu ingreso durante {choque['duracion_meses'][0]} a
  {choque['duracion_meses'][1]} meses, porque tu historial de un año no trae ese
  riesgo.
- Empezamos con lo que tienes en tu cuenta digital ({saldo_md}). No contamos CETES
  ni otras inversiones.
- Decimos que una casa te alcanza si la hipoteca no sube más de
  {r['incremento'] * 100:.0f} de cada 100 las veces que te quedarías sin dinero.
- Tasa, plazo, enganche y gastos de escrituración son supuestos que puedes mover,
  no la oferta de un banco.
""")
            st.caption(ui.md(nota_tasa.replace("de este cliente", "de tu crédito")))
        else:
            st.markdown(f"""
- Cada trayectoria arma los próximos {meses} meses eligiendo al azar meses del
  historial del cliente (ingresos y salidas reales, incluidas las deudas que ya paga).
- Además, cada mes hay {choque['probabilidad_mensual']:.0%} de probabilidad de perder el
  ingreso laboral durante {choque['duracion_meses'][0]} a {choque['duracion_meses'][1]}
  meses: el historial de un año no contiene ese riesgo.
- Saldo inicial: la cuenta digital ({saldo_md}). No cuenta CETES ni
  otros activos.
- Tasa, plazo, enganche y gastos de escrituración son supuestos editables
  del simulador, no condiciones de un banco.
- Los meses se eligen de forma independiente: el modelo no reproduce rachas ni
  estacionalidad.
""")

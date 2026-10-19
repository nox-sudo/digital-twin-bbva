"""
Vista 360 de un cliente: su perfil, su salud financiera comparada con
el portafolio, su flujo de efectivo mes a mes y a donde se va su
dinero. Es la vista que el simulador y el asistente van a extender.
"""

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import comun

st.title("Cliente")

cfg = comun.config()

if comun.gold_disponible():
    version = comun.version_gold()
    df = comun.portafolio(version)
    clientes = df["cliente_id"].tolist()

    preseleccionado = st.session_state.get("cliente_id", clientes[0])
    cliente_id = st.selectbox(
        "Cliente",
        clientes,
        index=clientes.index(preseleccionado) if preseleccionado in clientes else 0,
    )
    st.session_state["cliente_id"] = cliente_id
    fila = df.set_index("cliente_id").loc[cliente_id]

    def contexto(columna: str) -> str:
        """Texto de ayuda que ubica el valor del cliente en el portafolio."""
        percentil = comun.datos.percentil_en_portafolio(df, columna, fila[columna])
        mediana = df[columna].median()
        if pd.isna(percentil):
            return "Sin dato para este cliente."
        return (
            f"Mayor o igual que el {percentil:.0%} de los clientes. "
            f"Mediana del portafolio: {comun.pesos(mediana)}."
        )

    # --- Riesgo -----------------------------------------------------------
    banda = fila["banda_riesgo"]
    probabilidad = fila["probabilidad_impago"]
    color = comun.COLOR_BANDA.get(banda, "#9a9a96")
    st.markdown(
        f"<div style='display:flex;gap:12px;align-items:center;margin:4px 0 16px'>"
        f"<span style='width:12px;height:12px;border-radius:50%;background:{color};"
        f"display:inline-block'></span>"
        f"<span style='font-size:1.1rem'><b>Riesgo {banda}</b> · probabilidad de impago "
        f"{comun.porcentaje(probabilidad, 1)}</span></div>",
        unsafe_allow_html=True,
    )

    # --- Salud financiera -------------------------------------------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Ingreso mensual",
        comun.pesos(fila["ingreso_mensual_promedio"]),
        help=contexto("ingreso_mensual_promedio")
        + f" Promedio de los últimos {cfg['ventana_meses']} meses.",
    )
    c2.metric(
        "Gasto mensual",
        comun.pesos(fila["gasto_mensual_total"]),
        help=contexto("gasto_mensual_total")
        + f" Promedio de los últimos {cfg['ventana_meses']} meses.",
    )
    c3.metric(
        "Capacidad de ahorro",
        comun.pesos(fila["capacidad_ahorro"]),
        help=contexto("capacidad_ahorro")
        + " Ingreso menos compras, pagos de deuda y retiros, en promedio mensual"
        + f" de los últimos {cfg['ventana_meses']} meses.",
    )
    c4.metric(
        "Saldo disponible",
        comun.pesos(fila["saldo_liquido_disponible"]),
        help=contexto("saldo_liquido_disponible"),
    )

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Edad", f"{fila['edad_anios']:.0f} años")
    p2.metric("Antigüedad", f"{fila['antiguedad_dias'] / 365:.1f} años")
    productos = ["cuenta digital"]
    if fila["tiene_tarjeta_credito"]:
        productos.append("tarjeta")
    if fila["tiene_prestamo_personal"]:
        productos.append("préstamo")
    p3.metric("Productos", int(fila["numero_productos"]), help=", ".join(productos))
    p4.metric(
        "Deuda / ingreso",
        f"{fila['ratio_endeudamiento']:.2f}",
        help="Saldo de tarjeta y préstamo entre ingreso mensual. 0 si no tiene crédito.",
    )

    # --- Flujo mensual ------------------------------------------------------
    st.subheader("Flujo de efectivo mes a mes")
    flujo = comun.flujo(version, cliente_id)
    flujo["mes_nombre"] = flujo["mes"].map(comun.nombre_mes)
    largo = flujo.melt(
        id_vars=["mes", "mes_nombre", "neto"],
        value_vars=["entradas", "salidas"],
        var_name="tipo",
        value_name="monto",
    )
    barras = (
        alt.Chart(largo)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("mes_nombre:N", sort=None, title=None),
            xOffset=alt.XOffset("tipo:N", sort=["entradas", "salidas"]),
            y=alt.Y("monto:Q", title="Pesos (MXN)", axis=alt.Axis(format="~s")),
            color=alt.Color(
                "tipo:N",
                scale=alt.Scale(
                    domain=["entradas", "salidas"], range=[comun.AZUL, comun.NARANJA]
                ),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=[
                alt.Tooltip("mes_nombre:N", title="Mes"),
                alt.Tooltip("tipo:N", title="Tipo"),
                alt.Tooltip("monto:Q", title="Monto", format="$,.0f"),
                alt.Tooltip("neto:Q", title="Neto del mes", format="$,.0f"),
            ],
        )
        .properties(height=280)
    )
    st.altair_chart(barras, use_container_width=True)
    meses_negativos = int((flujo["neto"] < 0).sum())
    st.caption(
        f"Neto promedio: {comun.pesos(flujo['neto'].mean())} al mes. "
        f"Meses con salidas mayores que entradas: {meses_negativos} de {len(flujo)}."
    )

    # --- Salidas por categoria -------------------------------------------
    st.subheader("A dónde se va el dinero")
    categorias = comun.salidas_categoria(version, cliente_id)
    categorias["etiqueta"] = categorias["proporcion"].map(lambda p: f"{p:.0%}")
    base = alt.Chart(categorias).encode(
        y=alt.Y("categoria:N", sort="-x", title=None),
        x=alt.X("total:Q", title="Total del periodo (MXN)", axis=alt.Axis(format="~s")),
    )
    grafica = base.mark_bar(color=comun.AZUL, cornerRadiusEnd=4).encode(
        tooltip=[
            alt.Tooltip("categoria:N", title="Categoría"),
            alt.Tooltip("total:Q", title="Total", format="$,.0f"),
            alt.Tooltip("proporcion:Q", title="Proporción", format=".1%"),
        ]
    ) + base.mark_text(align="left", dx=6, color="#3d3d3a").encode(text="etiqueta:N")
    st.altair_chart(grafica.properties(height=alt.Step(26)), use_container_width=True)
    st.caption(
        "Todas las salidas del periodo: compras por categoría, pagos de deuda, "
        "retiros en efectivo y transferencias enviadas."
    )

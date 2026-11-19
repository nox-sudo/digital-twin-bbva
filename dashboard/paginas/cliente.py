"""
Vista 360 de un cliente: su perfil, su salud financiera comparada con
el portafolio, su flujo de efectivo mes a mes y a donde se va su
dinero. Es la vista que el simulador y el asistente van a extender.
"""

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import acceso, comun, ui

st.title("Cliente")
acceso.requiere_rol("admin")

cfg = comun.config()

if comun.gold_disponible():
    version = comun.version_gold()
    df = comun.portafolio(version)
    clientes = df["cliente_id"].tolist()

    cliente_id = acceso.cliente_actual(clientes)
    fila = df.set_index("cliente_id").loc[cliente_id]

    def contexto(columna: str) -> str:
        """Texto de ayuda que ubica el valor del cliente en el portafolio."""
        percentil = comun.datos.percentil_en_portafolio(df, columna, fila[columna])
        mediana = df[columna].median()
        if pd.isna(percentil):
            return "Sin dato para este cliente."
        return (
            f"Mayor o igual que el {ui.formatear_porcentaje(percentil)} de los "
            f"clientes. Mediana del portafolio: {ui.formatear_mxn(mediana)}."
        )

    # --- Riesgo -----------------------------------------------------------
    banda = fila["banda_riesgo"]
    probabilidad = fila["probabilidad_impago"]
    st.markdown(
        "<div style='display:flex;flex-wrap:wrap;gap:12px;align-items:center;"
        "margin:4px 0 16px'>"
        + ui.html_semaforo(banda)
        + f"<span style='font-size:1.05rem'>{ui.nombre_kpi('probabilidad_impago')} "
        f"<b>{ui.formatear_porcentaje(probabilidad, 1)}</b></span></div>",
        unsafe_allow_html=True,
    )

    # --- Salud financiera -------------------------------------------------
    ventana = {"ventana": cfg["ventana_meses"]}
    columnas = st.columns(4)
    for columna, clave in zip(
        columnas,
        [
            "ingreso_mensual_promedio",
            "gasto_mensual_total",
            "capacidad_ahorro",
            "saldo_liquido_disponible",
        ],
    ):
        ui.tarjeta_kpi(
            ui.nombre_kpi(clave),
            ui.formatear_mxn(fila[clave]),
            ayuda=ui.ayuda(clave, contexto(clave), **ventana),
            contenedor=columna,
        )

    p1, p2, p3, p4 = st.columns(4)
    ui.tarjeta_kpi(
        ui.nombre_kpi("edad_anios"),
        f"{fila['edad_anios']:.0f} años",
        ayuda=ui.ayuda("edad_anios"),
        contenedor=p1,
    )
    ui.tarjeta_kpi(
        ui.nombre_kpi("antiguedad_anios"),
        f"{fila['antiguedad_dias'] / 365:.1f} años",
        ayuda=ui.ayuda("antiguedad_anios"),
        contenedor=p2,
    )
    productos = ["cuenta digital"]
    if fila["tiene_tarjeta_credito"]:
        productos.append("tarjeta")
    if fila["tiene_prestamo_personal"]:
        productos.append("préstamo")
    ui.tarjeta_kpi(
        ui.nombre_kpi("numero_productos"),
        str(int(fila["numero_productos"])),
        ayuda=ui.ayuda("numero_productos", "Tiene: " + ", ".join(productos) + "."),
        contenedor=p3,
    )
    ratio = fila["ratio_endeudamiento"]
    ui.tarjeta_kpi(
        ui.nombre_kpi("ratio_endeudamiento"),
        "Sin ingreso" if pd.isna(ratio) else f"{ratio:.2f}",
        ayuda=ui.ayuda("ratio_endeudamiento"),
        contenedor=p4,
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
    largo["monto_texto"] = largo["monto"].map(ui.formatear_mxn)
    largo["neto_texto"] = largo["neto"].map(ui.formatear_mxn)
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
                alt.Tooltip("monto_texto:N", title="Monto"),
                alt.Tooltip("neto_texto:N", title="Neto del mes"),
            ],
        )
        .properties(height=280)
    )
    st.altair_chart(barras, use_container_width=True)
    # El neto se resume sobre la misma ventana que las metricas de arriba;
    # la grafica muestra todo el historial.
    ventana = cfg["ventana_meses"]
    if len(flujo):
        desde = comun.datos.inicio_ventana(flujo["mes"].max(), ventana)
        en_ventana = flujo[flujo["mes"] >= desde]
        meses_negativos = int((en_ventana["neto"] < 0).sum())
        st.caption(
            f"Últimos {ventana} meses: neto promedio de "
            f"{ui.formatear_mxn(en_ventana['neto'].mean())} al mes; salidas mayores que "
            f"entradas en {meses_negativos} de {len(en_ventana)} meses. La gráfica "
            f"muestra todo el historial ({len(flujo)} meses)."
        )

    # --- Salidas por categoria -------------------------------------------
    st.subheader("A dónde se va el dinero")
    categorias = comun.salidas_categoria(version, cliente_id)
    categorias["etiqueta"] = categorias["proporcion"].map(ui.formatear_porcentaje)
    categorias["total_texto"] = categorias["total"].map(ui.formatear_mxn)
    categorias["proporcion_texto"] = categorias["proporcion"].map(
        lambda p: ui.formatear_porcentaje(p, 1)
    )
    categorias["categoria"] = categorias["categoria"].map(comun.nombre_categoria)
    base = alt.Chart(categorias).encode(
        y=alt.Y("categoria:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
        x=alt.X(
            "total:Q",
            title=f"Total de los últimos {cfg['ventana_meses']} meses (MXN)",
            axis=alt.Axis(format="~s"),
        ),
    )
    grafica = base.mark_bar(color=comun.AZUL, cornerRadiusEnd=4).encode(
        tooltip=[
            alt.Tooltip("categoria:N", title="Categoría"),
            alt.Tooltip("total_texto:N", title="Total"),
            alt.Tooltip("proporcion_texto:N", title="Proporción"),
        ]
    ) + base.mark_text(align="left", dx=6, color="#3d3d3a").encode(text="etiqueta:N")
    st.altair_chart(grafica.properties(height=alt.Step(26)), use_container_width=True)
    st.caption(
        f"Todas las salidas de los últimos {cfg['ventana_meses']} meses: compras "
        "por categoría, pagos de deuda, retiros en efectivo y transferencias "
        "enviadas."
    )

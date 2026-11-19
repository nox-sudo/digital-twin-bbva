"""
Vista ejecutiva del portafolio: cuantos clientes hay, como esta su
salud financiera y donde se concentra el riesgo. Responde a la
pregunta de un responsable de negocio: "a quien tengo que mirar".
"""

import altair as alt
import streamlit as st

from dashboard import acceso, comun, ui

st.title("Portafolio")
acceso.requiere_rol("admin")

cfg = comun.config()

if comun.gold_disponible():
    version = comun.version_gold()
    df = comun.portafolio(version)
    resumen = comun.datos.resumen_portafolio(df)
    # Cifras de la sesion base (el corte mas antiguo del historico), para
    # mostrar cuanto cambio el portafolio. None en la propia base o si
    # todavia no hay historico.
    base = comun.datos.resumen_base(cfg["resumen_portafolio"], resumen["fecha_corte"])

    def puntos(actual, de_base):
        if base is None or actual is None or de_base is None:
            return None
        return f"{(actual - de_base) * 100:+.1f} pts vs. base"

    st.caption(
        f"{resumen['clientes']:,} clientes · datos al {comun.fecha(resumen['fecha_corte'])}"
        + (
            f" · comparado con la base al {comun.fecha(base['fecha_corte'])}"
            if base is not None
            else ""
        )
    )

    ventana = {"ventana": cfg["ventana_meses"]}
    c1, c2, c3, c4 = st.columns(4)
    ui.tarjeta_kpi(
        ui.nombre_kpi("clientes"),
        f"{resumen['clientes']:,}",
        ayuda=ui.ayuda("clientes"),
        contenedor=c1,
    )
    ui.tarjeta_kpi(
        ui.nombre_kpi("ingreso_mediano"),
        ui.formatear_mxn(resumen["ingreso_mediano"]),
        delta=(
            f"{ui.formatear_mxn(resumen['ingreso_mediano'] - base['ingreso_mediano'])}"
            " vs. base"
            if base is not None
            else None
        ),
        ayuda=ui.ayuda("ingreso_mediano", **ventana),
        contenedor=c2,
    )
    ui.tarjeta_kpi(
        ui.nombre_kpi("pct_ahorro_negativo"),
        ui.formatear_porcentaje(resumen["pct_ahorro_negativo"]),
        delta=puntos(
            resumen["pct_ahorro_negativo"],
            base["pct_ahorro_negativo"] if base is not None else None,
        ),
        delta_color="inverse",
        ayuda=ui.ayuda("pct_ahorro_negativo", **ventana),
        contenedor=c3,
    )
    ui.tarjeta_kpi(
        ui.nombre_kpi("probabilidad_media"),
        ui.formatear_porcentaje(resumen["probabilidad_media"], 1),
        delta=puntos(
            resumen["probabilidad_media"],
            base["probabilidad_media"] if base is not None else None,
        ),
        # Gris y no verde: que baje no significa que el portafolio mejoro
        # (ver la nota de abajo).
        delta_color="off",
        ayuda=ui.ayuda(
            "probabilidad_media",
            tasa_objetivo=ui.formatear_porcentaje(cfg["tasa_objetivo"]),
        ),
        contenedor=c4,
    )
    if base is not None:
        st.caption(
            "Por qué la probabilidad media no sube: el modelo se reentrena en "
            "cada corrida y la etiqueta de impago se calibra para que la tasa "
            f"media sea {ui.formatear_porcentaje(cfg['tasa_objetivo'])}. "
            "El promedio del score se queda cerca de esa tasa aunque el "
            "portafolio empeore; el deterioro se ve en el ingreso, en quienes "
            "gastan más de lo que ganan y en la deriva del score "
            "(Observabilidad)."
        )
    if resumen["clientes_sin_score"]:
        st.info(
            f"{resumen['clientes_sin_score']} clientes no tienen score de riesgo: "
            "se completan al correr de nuevo la predicción de riesgo del pipeline."
        )

    # --- Riesgo ----------------------------------------------------------
    st.subheader("Dónde está el riesgo")
    bandas_cfg = comun.config()["bandas_riesgo"]
    limites = ", ".join(
        f"{b['nombre']} desde {ui.formatear_porcentaje(b['desde'])}" for b in bandas_cfg
    )

    col_bandas, col_hist = st.columns(2)
    with ui.contenedor("bandas", col_bandas):
        conteo = (
            df["banda_riesgo"]
            .value_counts()
            .reindex(comun.ORDEN_BANDAS)
            .dropna()
            .rename_axis("banda")
            .reset_index(name="clientes")
        )
        conteo["proporcion"] = conteo["clientes"] / conteo["clientes"].sum()
        conteo["proporcion_texto"] = conteo["proporcion"].map(
            lambda p: ui.formatear_porcentaje(p, 1)
        )
        conteo["etiqueta"] = conteo.apply(
            lambda f: f"{int(f['clientes'])} ({ui.formatear_porcentaje(f['proporcion'])})",
            axis=1,
        )
        base = alt.Chart(conteo).encode(
            y=alt.Y("banda:N", sort=comun.ORDEN_BANDAS, title=None),
            x=alt.X("clientes:Q", title="Clientes"),
        )
        barras = base.mark_bar(cornerRadiusEnd=4, height=28).encode(
            color=alt.Color(
                "banda:N",
                scale=alt.Scale(
                    domain=list(comun.COLOR_BANDA),
                    range=list(comun.COLOR_BANDA.values()),
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("banda:N", title="Banda"),
                alt.Tooltip("clientes:Q", title="Clientes"),
                alt.Tooltip("proporcion_texto:N", title="Proporción"),
            ],
        )
        etiquetas = base.mark_text(align="left", dx=6, color="#3d3d3a").encode(
            text="etiqueta:N"
        )
        st.altair_chart(
            (barras + etiquetas).properties(height=200), use_container_width=True
        )
        st.caption(f"Bandas de riesgo: {limites}.")

    with ui.contenedor("distribucion", col_hist):
        hist = (
            alt.Chart(df.dropna(subset=["probabilidad_impago"]))
            .mark_bar(color=comun.AZUL, cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
            .encode(
                x=alt.X(
                    "probabilidad_impago:Q",
                    bin=alt.Bin(step=0.05),
                    title="Probabilidad de impago",
                    axis=alt.Axis(format=".0%"),
                ),
                y=alt.Y("count():Q", title="Clientes"),
                tooltip=[alt.Tooltip("count():Q", title="Clientes")],
            )
            .properties(height=200)
        )
        st.altair_chart(hist, use_container_width=True)
        st.caption("Distribución del score de riesgo, en tramos de 5 puntos.")

    # --- Riesgo por segmento ---------------------------------------------
    st.subheader("Riesgo por categoría de gasto dominante")
    segmento = (
        df.groupby("categoria_gasto_dominante", dropna=False)
        .agg(
            clientes=("cliente_id", "count"),
            probabilidad_media=("probabilidad_impago", "mean"),
            ahorro_mediano=("capacidad_ahorro", "median"),
        )
        .reset_index()
        .rename(columns={"categoria_gasto_dominante": "categoria"})
        .sort_values("probabilidad_media", ascending=False)
    )
    segmento["categoria"] = segmento["categoria"].map(comun.nombre_categoria)
    segmento["probabilidad_texto"] = segmento["probabilidad_media"].map(
        lambda p: ui.formatear_porcentaje(p, 1)
    )
    segmento["ahorro_texto"] = segmento["ahorro_mediano"].map(ui.formatear_mxn)
    segmento_chart = (
        alt.Chart(segmento)
        .mark_bar(color=comun.AZUL, cornerRadiusEnd=4)
        .encode(
            y=alt.Y(
                "categoria:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)
            ),
            x=alt.X(
                "probabilidad_media:Q",
                title="Probabilidad media de impago",
                axis=alt.Axis(format=".0%"),
            ),
            tooltip=[
                alt.Tooltip("categoria:N", title="Categoría"),
                alt.Tooltip("clientes:Q", title="Clientes"),
                alt.Tooltip(
                    "probabilidad_texto:N", title=ui.nombre_kpi("probabilidad_media")
                ),
                alt.Tooltip("ahorro_texto:N", title="Capacidad de ahorro mediana"),
            ],
        )
        .properties(height=alt.Step(26))
    )
    st.altair_chart(segmento_chart, use_container_width=True)
    st.caption(
        "Categoría en la que cada cliente concentra la mayor parte de sus compras. "
        "Con pocos clientes por categoría, las diferencias pequeñas no son concluyentes."
    )

    # --- Flujo del portafolio --------------------------------------------
    st.subheader("Flujo de efectivo del portafolio")
    flujo = comun.flujo(version)
    flujo["mes_nombre"] = flujo["mes"].map(comun.nombre_mes)
    largo = flujo.melt(
        id_vars=["mes", "mes_nombre"],
        value_vars=["entradas", "salidas"],
        var_name="tipo",
        value_name="monto",
    )
    largo["monto_texto"] = largo["monto"].map(ui.formatear_mxn)
    lineas = (
        alt.Chart(largo)
        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60, filled=True))
        .encode(
            x=alt.X("mes_nombre:O", sort=None, title=None),
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
            ],
        )
        .properties(height=260)
    )
    st.altair_chart(lineas, use_container_width=True)
    st.caption(
        "Entradas: nómina, depósitos y transferencias recibidas. Salidas: compras, "
        "pagos de deuda, retiros y transferencias enviadas."
    )

    # --- Clientes a revisar -----------------------------------------------
    st.subheader("Clientes a revisar")
    st.caption(
        "Mayor probabilidad de impago primero. Selecciona una fila para ver al cliente."
    )
    tabla = (
        df.sort_values("probabilidad_impago", ascending=False)
        .head(25)[
            [
                "cliente_id",
                "banda_riesgo",
                "probabilidad_impago",
                "ingreso_mensual_promedio",
                "capacidad_ahorro",
                "ratio_endeudamiento",
                "uso_linea_credito",
            ]
        ]
        .reset_index(drop=True)
    )
    vista, columnas = ui.preparar_tabla(tabla)
    columnas["cliente_id"] = "Cliente"
    columnas["probabilidad_impago"] = ui.columna_porcentaje(
        ui.nombre_kpi("probabilidad_impago"), 1, barra=True
    )
    seleccion = st.dataframe(
        vista,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config=columnas,
    )
    filas = seleccion.selection.rows if seleccion else []
    if filas:
        st.session_state["cliente_id"] = tabla.loc[filas[0], "cliente_id"]
        st.switch_page(st.session_state["pagina_cliente"])

    with st.expander("Ver todos los clientes"):
        vista, columnas = ui.preparar_tabla(
            df.drop(columns=["fecha_calculo"], errors="ignore")
        )
        st.dataframe(
            vista, hide_index=True, use_container_width=True, column_config=columnas
        )

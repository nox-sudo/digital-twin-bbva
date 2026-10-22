"""
Vista de observabilidad tecnica: si la ultima corrida de cada capa
termino bien, cuanto tardo, que se fue a cuarentena y si las tablas de
Gold son coherentes entre si. Responde a la pregunta de quien opera el
pipeline: "puedo confiar en lo que muestran las otras dos vistas".
"""

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import acceso, comun
from src.monitoreo.psi import TEMPORAL
from src.monitoreo.psi import cargar_config as cargar_config_psi

st.title("Observabilidad del pipeline")
acceso.requiere_rol("admin")

cfg = comun.config()
corridas = comun.datos.corridas(cfg["logs"])

if corridas.empty:
    st.warning(
        f"No hay logs de corridas en {cfg['logs']}. Corre el pipeline para generarlos."
    )
else:
    # --- Ultima corrida por capa -----------------------------------------
    st.subheader("Última corrida por capa")
    columnas = st.columns(3)
    for columna, capa in zip(columnas, ["bronze", "silver", "gold"]):
        de_capa = corridas[corridas["capa"] == capa]
        if de_capa.empty:
            columna.metric(capa.capitalize(), "sin corridas")
            continue
        ultima = de_capa.iloc[-1]
        ok = ultima["estatus"] == "SUCCESS"
        columna.metric(capa.capitalize(), "Correcta" if ok else "Fallida")
        duracion = ultima["duracion_segundos"]
        columna.caption(
            (f"{duracion:.1f} s · " if pd.notna(duracion) else "")
            + (
                f"{ultima['inicio']:%d/%m/%Y %H:%M} · "
                if pd.notna(ultima["inicio"])
                else ""
            )
            + f"corridas registradas: {len(de_capa)}"
        )
        if not ok:
            columna.error(ultima["detalle"])

    # --- Coherencia de Gold ----------------------------------------------
    st.subheader("Coherencia de Gold")
    if comun.gold_disponible():
        chequeos = comun.datos.consistencia_gold(cfg["gold_duckdb"])
        chequeos["resultado"] = chequeos["ok"].map({True: "Correcto", False: "Revisar"})
        st.dataframe(
            chequeos.drop(columns=["ok"]),
            hide_index=True,
            use_container_width=True,
            column_config={
                "comprobacion": "Comprobación",
                "observado": "Observado",
                "esperado": "Esperado",
                "resultado": "Resultado",
            },
        )
        if not chequeos["ok"].all():
            st.warning(
                "Alguna tabla de Gold no coincide con las demas: probablemente una "
                "corrida parcial. Vuelve a correr Gold y la predicción de riesgo."
            )

    # --- Silver: calidad por entidad -------------------------------------
    st.subheader("Calidad en Silver, última corrida")
    silver = comun.datos.entidades_ultima_corrida(cfg["logs"], "silver")
    if silver.empty:
        st.info("Todavia no hay corridas de Silver registradas.")
    else:
        en_cuarentena = int(silver["filas_cuarentena"].sum())
        st.caption(
            "Filas que no pasaron alguna regla de config/business_rules.yaml. "
            "Se aislan en cuarentena sin detener el pipeline."
            if en_cuarentena
            else "Ninguna fila fue a cuarentena en la última corrida."
        )
        st.dataframe(
            silver,
            hide_index=True,
            use_container_width=True,
            column_config={
                "entidad": "Entidad",
                "duracion_segundos": st.column_config.NumberColumn(
                    "Duración (s)", format="%.1f"
                ),
                "filas_entrada": st.column_config.NumberColumn("Entrada", format="%,d"),
                "duplicados_removidos": st.column_config.NumberColumn(
                    "Duplicados", format="%,d"
                ),
                "filas_validas": st.column_config.NumberColumn("Válidas", format="%,d"),
                "filas_cuarentena": st.column_config.NumberColumn(
                    "Cuarentena", format="%,d"
                ),
                "reglas_violadas": "Reglas violadas",
            },
        )

    # --- Duracion entre corridas -----------------------------------------
    st.subheader("Duración por corrida")
    # Solo las capas del lakehouse: el monitoreo de deriva tambien escribe
    # en data/logs/ y aparece en "Todas las corridas registradas".
    historico = corridas[corridas["capa"].isin(["bronze", "silver", "gold"])].dropna(
        subset=["inicio", "duracion_segundos"]
    )
    grafica = (
        alt.Chart(historico)
        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60, filled=True))
        .encode(
            x=alt.X("inicio:T", title=None, axis=alt.Axis(format="%d/%m %H:%M")),
            # Escala logaritmica: una corrida congelada (por ejemplo, la Mac en
            # reposo) tarda cien veces mas y en escala lineal aplastaba a las
            # demas en una linea plana.
            y=alt.Y(
                "duracion_segundos:Q",
                title="Segundos (escala logarítmica)",
                scale=alt.Scale(type="log"),
            ),
            color=alt.Color(
                "capa:N",
                scale=alt.Scale(
                    domain=["bronze", "silver", "gold"],
                    range=[comun.AZUL, comun.NARANJA, comun.AQUA],
                ),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=[
                alt.Tooltip("capa:N", title="Capa"),
                alt.Tooltip("inicio:T", title="Inicio", format="%Y-%m-%d %H:%M"),
                alt.Tooltip("duracion_segundos:Q", title="Segundos", format=".1f"),
                alt.Tooltip("estatus:N", title="Estatus"),
            ],
        )
        .properties(height=260)
    )
    st.altair_chart(grafica, use_container_width=True)
    st.caption(
        "Una corrida que tarda mucho más que las anteriores con el mismo volumen "
        "es la primera señal de un problema. Eje en escala logarítmica: cada "
        "marca es diez veces la anterior, para que se vean a la vez las "
        "corridas normales y las que se salen."
    )

    with st.expander("Todas las corridas registradas"):
        st.dataframe(
            corridas.sort_values("inicio", ascending=False),
            hide_index=True,
            use_container_width=True,
        )

# --- Deriva de datos (PSI) -------------------------------------------------
st.subheader("Deriva de datos (PSI)")
historico_psi = comun.datos.deriva_psi(cfg["deriva_psi"])
if historico_psi.empty:
    st.info(
        "Todavía no hay corridas del monitoreo de deriva. Las agrega el último "
        "paso del DAG (monitor_drift.py)."
    )
else:
    cfg_psi = cargar_config_psi(cfg["monitoreo_psi"])
    temporales = set(cfg_psi.variables_temporales)
    tendencia = comun.datos.tendencia_psi(historico_psi)
    # El estado se guarda con la configuracion vigente en cada corrida. Las
    # variables que hoy son temporales se muestran como tales tambien en
    # corridas anteriores, para que el conteo y los paneles sigan la
    # configuracion actual; el historico no se modifica.
    reclasificadas = (
        (tendencia["tipo"] == "feature")
        & tendencia["variable"].isin(temporales)
        & (tendencia["estado"] != TEMPORAL)
    )
    tendencia.loc[reclasificadas, "estado"] = TEMPORAL
    ultimo_corte = tendencia["fecha_corte"].max()
    ultima = tendencia[tendencia["fecha_corte"] == ultimo_corte]
    conteo = ultima["estado"].value_counts()

    st.caption(
        f"Contra la referencia {tendencia['referencia_id'].iloc[0]}: "
        f"{tendencia['fecha_corte'].nunique()} cortes medidos, el último al "
        f"{comun.fecha(ultimo_corte)}. Cada variable se compara con los deciles "
        "congelados de la población de entrenamiento del modelo base."
        + (
            " Las variables temporales se muestran con la configuración actual "
            "aunque la corrida las haya clasificado de otra forma."
            if reclasificadas.any()
            else ""
        )
    )
    columnas_estado = st.columns(4)
    for columna, (estado, etiqueta) in zip(
        columnas_estado,
        [
            ("reentrenar", "Reentrenar"),
            ("vigilar", "Vigilar"),
            ("estable", "Estables"),
            ("temporal", "Temporales"),
        ],
    ):
        columna.metric(etiqueta, int(conteo.get(estado, 0)))

    # Small multiples: un panel por variable, una sola linea cada uno. Con 18
    # variables, una grafica con una linea por variable necesitaria mas
    # colores de los que se distinguen; cada panel lleva su nombre.
    maximos = tendencia.groupby("variable")["psi"].max()
    tipo_de = tendencia.groupby("variable")["tipo"].first()
    ultimo_psi = ultima.set_index("variable")["psi"]
    con_movimiento = [
        v
        for v in maximos.index
        if (tipo_de[v] == "score" or maximos[v] >= cfg_psi.umbral_vigilar)
        and v not in temporales
    ]
    por_defecto = sorted(
        con_movimiento, key=lambda v: (tipo_de[v] != "score", -ultimo_psi.get(v, 0))
    )
    seleccion = st.multiselect(
        "Variables",
        sorted(maximos.index),
        default=por_defecto,
        help="Por defecto: el score y las variables que alguna vez pasaron el "
        "umbral de vigilar, sin las temporales.",
    )
    if seleccion:
        grafica_datos = tendencia[tendencia["variable"].isin(seleccion)].copy()
        grafica_datos["panel"] = grafica_datos["variable"] + grafica_datos[
            "variable"
        ].map(
            lambda v: (
                " (score)"
                if tipo_de[v] == "score"
                else " (temporal)" if v in temporales else ""
            )
        )
        # Un tick por corte medido (no por mes calendario): la base es el
        # 30 de septiembre y cada entrega agrega el fin del mes siguiente.
        grafica_datos["corte"] = pd.to_datetime(
            grafica_datos["fecha_corte"]
        ).dt.strftime("%Y-%m")
        # Eje desde 0 y al menos hasta 0.3, para que la banda de reentrenar
        # siempre se vea aunque ninguna variable la haya alcanzado.
        techo_psi = max(
            cfg_psi.umbral_reentrenar * 1.2, float(grafica_datos["psi"].max()) * 1.05
        )
        orden_paneles = [
            grafica_datos.loc[grafica_datos["variable"] == v, "panel"].iloc[0]
            for v in seleccion
        ]
        linea = (
            alt.Chart()
            .mark_line(
                color=comun.AZUL,
                strokeWidth=2,
                point=alt.OverlayMarkDef(size=60, filled=True, color=comun.AZUL),
            )
            .encode(
                x=alt.X(
                    "corte:O",
                    title=None,
                    sort="ascending",
                    axis=alt.Axis(labelAngle=0),
                ),
                y=alt.Y("psi:Q", title="PSI", scale=alt.Scale(domain=[0, techo_psi])),
                tooltip=[
                    alt.Tooltip("variable:N", title="Variable"),
                    alt.Tooltip("fecha_corte:T", title="Corte", format="%Y-%m-%d"),
                    alt.Tooltip("psi:Q", title="PSI", format=".3f"),
                    alt.Tooltip("estado:N", title="Estado"),
                ],
            )
        )
        umbrales = [
            alt.Chart()
            .mark_rule(color="#9a9a96", strokeDash=[4, 4], strokeWidth=1)
            .encode(y=alt.datum(valor))
            for valor in (cfg_psi.umbral_vigilar, cfg_psi.umbral_reentrenar)
        ]
        # Una grafica por panel en una cuadricula de Streamlit, a todo el ancho
        # de su columna: 3 por fila en escritorio, apiladas en pantallas
        # angostas. Un facet de Altair tiene ancho fijo y quedaba chico en
        # escritorio o se salia de la pantalla en movil. El eje Y es el mismo
        # en todos los paneles (techo_psi), para poder compararlos.
        por_fila = 3
        for inicio in range(0, len(orden_paneles), por_fila):
            columnas_panel = st.columns(por_fila)
            for columna, panel in zip(
                columnas_panel, orden_paneles[inicio : inicio + por_fila]  # noqa: E203
            ):
                columna.altair_chart(
                    alt.layer(
                        linea,
                        *umbrales,
                        data=grafica_datos[grafica_datos["panel"] == panel],
                    ).properties(height=170, title=panel),
                    use_container_width=True,
                )
        st.caption(
            f"Líneas punteadas: {cfg_psi.umbral_vigilar:.2f} (vigilar) y "
            f"{cfg_psi.umbral_reentrenar:.2f} (reentrenar). Las variables "
            "temporales derivan por el paso del tiempo y no generan alerta "
            "(docs/monitoreo-deriva.md)."
        )

    with st.expander("Última corrida, todas las variables"):
        st.dataframe(
            ultima.sort_values("psi", ascending=False)[
                ["variable", "tipo", "psi", "estado"]
            ],
            hide_index=True,
            use_container_width=True,
            column_config={
                "variable": "Variable",
                "tipo": "Tipo",
                "psi": st.column_config.NumberColumn("PSI", format="%.3f"),
                "estado": "Estado",
            },
        )

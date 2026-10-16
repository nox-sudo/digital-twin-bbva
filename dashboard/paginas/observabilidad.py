"""
Vista de observabilidad tecnica: si la ultima corrida de cada capa
termino bien, cuanto tardo, que se fue a cuarentena y si las tablas de
Gold son coherentes entre si. Responde a la pregunta de quien opera el
pipeline: "puedo confiar en lo que muestran las otras dos vistas".
"""

import altair as alt
import pandas as pd
import streamlit as st

from dashboard import comun

st.title("Observabilidad del pipeline")

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
    historico = corridas.dropna(subset=["inicio", "duracion_segundos"])
    grafica = (
        alt.Chart(historico)
        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=60, filled=True))
        .encode(
            x=alt.X("inicio:T", title=None, axis=alt.Axis(format="%d/%m %H:%M")),
            y=alt.Y("duracion_segundos:Q", title="Segundos"),
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
        "es la primera señal de un problema."
    )

    with st.expander("Todas las corridas registradas"):
        st.dataframe(
            corridas.sort_values("inicio", ascending=False),
            hide_index=True,
            use_container_width=True,
        )

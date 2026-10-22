"""
Asistente conversacional sobre el cliente seleccionado y el portafolio.
Corre con un modelo local de Ollama (config/asistente.yaml): el modelo elige
herramientas y Python calcula las cifras con las mismas consultas que las
demas vistas (asistente/herramientas.py). Cada respuesta muestra que
herramientas uso y que devolvieron.

Necesita el grupo de dependencias "asistente" y Ollama corriendo:
    uv run --group dashboard --group asistente streamlit run dashboard/app.py
"""

import streamlit as st

from asistente.conversacion import (
    Configuracion,
    ErrorConexion,
    ModeloOllama,
    responder,
)
from asistente.herramientas import Contexto
from dashboard import acceso, comun

st.title("Asistente")
acceso.requiere_rol("admin", "cliente")

EJEMPLOS = [
    "¿Cuál es mi riesgo de impago?",
    "¿En qué se me va el dinero?",
    "¿Cuál fue mi flujo en los últimos 3 meses?",
    "¿Cómo estoy comparado con los demás en capacidad de ahorro?",
]


@st.cache_resource(show_spinner=False)
def modelo(cfg: Configuracion) -> ModeloOllama:
    return ModeloOllama(cfg)


def mostrar_pasos(pasos) -> None:
    if not pasos:
        return
    with st.expander("Cómo se obtuvo", expanded=False):
        for paso in pasos:
            st.markdown(
                f"`{paso.herramienta}` con {paso.argumentos or 'sin argumentos'}"
            )
            st.json(paso.resultado, expanded=False)


if comun.gold_disponible():
    cfg = Configuracion.desde_yaml()
    version = comun.version_gold()
    clientes = comun.portafolio(version)["cliente_id"].tolist()

    # El cliente sale de la sesion (o del selector del admin), nunca del
    # modelo ni de la URL.
    cliente_id = acceso.cliente_actual(clientes)
    st.caption(
        f"Modelo local {cfg.modelo} en Ollama. Las cifras salen de las mismas "
        "consultas que las demás vistas; el modelo solo elige qué consultar y "
        "redacta. Datos sintéticos."
    )

    conversaciones = st.session_state.setdefault("asistente_conversaciones", {})
    historial = conversaciones.setdefault(cliente_id, [])

    for turno in historial:
        with st.chat_message("user"):
            st.markdown(turno["pregunta"])
        with st.chat_message("assistant"):
            st.markdown(turno["respuesta"].texto)
            mostrar_pasos(turno["respuesta"].pasos)

    if not historial:
        st.markdown("Por ejemplo:")
        columnas = st.columns(len(EJEMPLOS))
        for columna, ejemplo in zip(columnas, EJEMPLOS):
            if columna.button(ejemplo, use_container_width=True):
                st.session_state["asistente_pregunta"] = ejemplo
                st.rerun()

    pregunta = st.chat_input("Pregunta sobre este cliente o el portafolio")
    pregunta = pregunta or st.session_state.pop("asistente_pregunta", None)
    if pregunta:
        with st.chat_message("user"):
            st.markdown(pregunta)
        with st.chat_message("assistant"):
            try:
                with st.spinner("Consultando..."):
                    respuesta = responder(
                        pregunta,
                        [(t["pregunta"], t["respuesta"].texto) for t in historial],
                        Contexto.desde_config(cliente_id),
                        modelo(cfg),
                        comun.flujo(version, cliente_id)["mes"].max(),
                        cfg.max_vueltas,
                        cfg.historial_turnos,
                    )
            except ErrorConexion as e:
                st.error(str(e))
                st.stop()
            st.markdown(respuesta.texto)
            mostrar_pasos(respuesta.pasos)
        historial.append({"pregunta": pregunta, "respuesta": respuesta})

    if historial and st.button("Borrar conversación"):
        conversaciones[cliente_id] = []
        st.rerun()

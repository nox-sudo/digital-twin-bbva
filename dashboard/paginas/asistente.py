"""
Asistente conversacional sobre el cliente seleccionado y, para el admin,
sobre el portafolio.
Corre con un modelo local de Ollama (config/asistente.yaml): el modelo elige
herramientas y Python calcula las cifras con las mismas consultas que las
demas vistas (asistente/herramientas.py). Debajo de cada respuesta va una
linea en español con el origen de los datos, sin nombres de herramientas,
argumentos ni resultados crudos: la traza completa queda en la evaluacion
automatizada (experimentos/eval_asistente.py).

El rol sale del usuario de la sesion (acceso.usuario_actual()), nunca del
modelo ni de la URL. Con el se arma el Contexto y el modelo: el de un
cliente solo tiene las herramientas de cliente, y la cache de modelos se
separa por rol para que un cliente nunca reciba el modelo con las
herramientas del admin.

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
from dashboard import acceso, comun, ui
from src.simulacion.supuestos import cargar_simulacion

st.title("Asistente")
acceso.requiere_rol("admin", "cliente")

# Preguntas de ejemplo por rol. Solo preguntas que aprobaron 9 de 9 corridas
# (3 clientes por 3 repeticiones) en la evaluacion del 2026-11-05
# (docs/evaluacion-asistente.md, "Cifras de todo el banco solo para el
# admin"); entre parentesis, banco y caso de experimentos/eval_asistente*.yaml.
# Las del cliente se evaluaron en primera persona con rol cliente; las del
# admin, con rol admin. No hay preguntas del admin sobre un cliente en
# tercera persona evaluadas, asi que el admin solo tiene las del portafolio.
# Ademas, las ocho se corrieron sobre la instancia de la demo (2026-11-17).
# v2 siguiente_paso_escalera se cambio por v4 paso_actual porque ahi pedia un
# dato en vez de responder. v2 porcentaje_gasta_mas daba 15.79 % (la
# probabilidad media) en vez de 56.6 %; volvio despues de agregar la lectura
# de resumen_portafolio, con la que da 56.6 % en 3 de 3 corridas
# (docs/evaluacion-asistente.md).
EJEMPLOS = {
    "cliente": [
        "¿En qué categoría gasto más?",  # v1 categoria_mayor_gasto
        "¿Qué tan probable es que me atrase en mis pagos?",  # v3 mi_riesgo
        "¿Qué paso me toca en mi escalera financiera y qué me falta?",  # v4 paso_actual
        "¿Ahorro más o menos que el resto de los clientes?",  # v3 ahorro_contra_los_demas
    ],
    "admin": [
        "¿Cuántos clientes están en riesgo alto?",  # v1 clientes_riesgo_alto
        "¿Qué porcentaje de los clientes gasta más de lo que gana?",  # v2 porcentaje_gasta_mas
        "¿Cuál es el ingreso mediano de los clientes del banco?",  # v2 ingreso_mediano_clientes
        # v3 probabilidad_media_portafolio
        "¿Cuál es la probabilidad media de impago de todo el portafolio?",
    ],
}

DESCRIPCION = {
    "cliente": "Respondo sobre tus cuentas, movimientos, crédito y riesgo con tus datos.",
    "admin": "Respondo sobre este cliente y sobre el portafolio con los datos del banco.",
}

TEXTO_ENTRADA = {
    "admin": "Pregunta sobre este cliente o el portafolio",
    "cliente": "Pregunta sobre tus finanzas",
}

PENSANDO = {
    "cliente": "Revisando tus datos...",
    "admin": "Revisando los datos...",
}


@st.cache_data
def trayectorias() -> int:
    """Escenarios de la simulacion (config/simulacion.yaml), los mismos que
    usan simular_hipoteca y evaluar_compra."""
    cfg = cargar_simulacion(comun.RAIZ / "config" / "simulacion.yaml")
    return int(cfg["simulacion"]["trayectorias"])


def fuentes(rol: str) -> dict[str, str]:
    """Herramienta -> de donde salio la respuesta, en una frase. Para el
    cliente en segunda persona; para el admin, en tercera."""
    tu, tus = ("tu", "tus") if rol == "cliente" else ("su", "sus")
    simulacion = f"Simulación de {trayectorias():,} escenarios con {tu} historial"
    return {
        "perfil_cliente": f"Con {tu} perfil y {tus} saldos al último corte",
        "gasto_por_categoria": f"Calculado con {tus} movimientos por categoría",
        "flujo_mensual": f"Calculado con lo que entró y salió de {tu} cuenta mes a mes",
        "comparar_con_portafolio": "Comparado con los demás clientes del banco",
        "simular_hipoteca": simulacion,
        "evaluar_compra": simulacion,
        "escalera_financiera": (
            f"Según {tu} paso en la escalera financiera, con {tus} pagos de deuda "
            f"y {tu} saldo"
        ),
        "resumen_portafolio": "Con las cifras de todo el portafolio al último corte",
        "buscar_documentacion": "Según la documentación del proyecto",
    }


def mostrar_fuentes(pasos, rol: str) -> None:
    """Una linea por fuente distinta, en el orden en que se consultaron. Una
    herramienta que no este en el diccionario no se nombra."""
    frases = fuentes(rol)
    vistas = []
    for paso in pasos:
        frase = frases.get(paso.herramienta)
        if frase and frase not in vistas:
            vistas.append(frase)
    for frase in vistas:
        st.caption(frase + ".")


@st.cache_resource(show_spinner=False)
def modelo(cfg: Configuracion, rol: str) -> ModeloOllama:
    # rol es parte de la llave de la cache: un modelo por rol.
    return ModeloOllama(cfg, rol)


if comun.gold_disponible():
    cfg = Configuracion.desde_yaml()
    version = comun.version_gold()
    clientes = comun.portafolio(version)["cliente_id"].tolist()

    # El cliente y el rol salen de la sesion (el cliente, o el selector del
    # admin), nunca del modelo ni de la URL.
    rol = acceso.usuario_actual().rol
    cliente_id = acceso.cliente_actual(clientes, label_visibility="visible")
    st.caption(DESCRIPCION[rol])

    conversaciones = st.session_state.setdefault("asistente_conversaciones", {})
    historial = conversaciones.setdefault(cliente_id, [])

    for turno in historial:
        with st.chat_message("user"):
            st.markdown(turno["pregunta"])
        with st.chat_message("assistant"):
            st.markdown(turno["respuesta"].texto)
            mostrar_fuentes(turno["respuesta"].pasos, rol)

    # La pregunta en curso se lee antes de dibujar los ejemplos: mientras el
    # modelo responde, los botones ya no se muestran.
    pregunta = st.chat_input(TEXTO_ENTRADA[rol])
    pregunta = pregunta or st.session_state.pop("asistente_pregunta", None)

    if not historial and not pregunta:
        st.markdown("Puedes empezar con una de estas:")
        ejemplos = EJEMPLOS[rol]
        for inicio in range(0, len(ejemplos), 2):
            columnas = st.columns(2)
            fila = ejemplos[inicio:][:2]
            for columna, ejemplo in zip(columnas, fila):
                if columna.button(ejemplo, use_container_width=True):
                    st.session_state["asistente_pregunta"] = ejemplo
                    st.rerun()

    if pregunta:
        with st.chat_message("user"):
            st.markdown(pregunta)
        with st.chat_message("assistant"):
            try:
                with st.spinner(PENSANDO[rol]):
                    respuesta = responder(
                        pregunta,
                        [(t["pregunta"], t["respuesta"].texto) for t in historial],
                        Contexto.desde_config(cliente_id, rol),
                        modelo(cfg, rol),
                        comun.flujo(version, cliente_id)["mes"].max(),
                        cfg.max_vueltas,
                        cfg.historial_turnos,
                    )
            except ErrorConexion as e:
                # El cliente lee que el asistente no esta disponible; el
                # admin ve ademas la causa y como arreglarla.
                ui.estado_error(
                    "El asistente no está disponible en este momento. "
                    "Intenta de nuevo en unos minutos.",
                    detalle=str(e),
                )
                st.stop()
            st.markdown(respuesta.texto)
            mostrar_fuentes(respuesta.pasos, rol)
        historial.append({"pregunta": pregunta, "respuesta": respuesta})

    if historial and st.button("Borrar conversación"):
        conversaciones[cliente_id] = []
        st.rerun()

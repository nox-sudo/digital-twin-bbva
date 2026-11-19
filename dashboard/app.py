"""
dashboard/app.py

Punto de entrada del dashboard del Gemelo Digital Financiero. Vistas
sobre la misma capa Gold:

  - Portafolio: vista ejecutiva, donde se concentra el riesgo.
  - Cliente: vista 360 de una persona.
  - Simulador de hipoteca: escenario Monte Carlo sobre el flujo del cliente.
  - Asistente: preguntas en lenguaje natural con un modelo local (Ollama).
  - Observabilidad: estado de las corridas y coherencia de Gold.

Pide iniciar sesion (dashboard/acceso.py). El admin ve todas las vistas; un
cliente solo las suyas (Cliente, Simulador y Asistente), siempre sobre sus
propios datos. Los usuarios se crean con generate_credentials.py.

Solo lee: DuckDB en modo solo lectura y los logs JSON de cada corrida.
No necesita Spark ni ningun servicio de Docker Compose.

Uso, desde la raiz del repositorio:
    uv run --group dashboard streamlit run dashboard/app.py
Con el asistente (necesita Ollama, ver docs/ejecucion-local.md):
    uv run --group dashboard --group asistente streamlit run dashboard/app.py
"""

import logging
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard import acceso, ui  # noqa: E402

st.set_page_config(page_title="Gemelo Digital Financiero", layout="wide")
ui.estilos()

PAGINAS = Path(__file__).resolve().parent / "paginas"


usuario = acceso.usuario_actual()
if usuario is None:
    acceso.pantalla_login()
    st.stop()

paginas = acceso.paginas_por_rol(usuario.rol, PAGINAS)
if usuario.rol == "admin":
    # La tabla de clientes a revisar del portafolio salta a la vista de
    # cliente; guarda aqui la referencia a la pagina para no duplicar rutas.
    st.session_state["pagina_cliente"] = paginas["Negocio"][1]
navegacion = st.navigation(paginas)
ui.sesion(usuario, acceso.cerrar_sesion)
st.sidebar.caption(
    "Gemelo Digital Financiero · Path Data Engineering BBVA × Tecmilenio"
)
try:
    navegacion.run()
except Exception as e:
    # Un error que ninguna vista previo: mensaje en lenguaje humano y, solo
    # para el admin, la causa. st.stop() y st.rerun() no pasan por aqui
    # (no heredan de Exception). El traceback queda en la consola.
    ui.estado_error(
        "No pudimos cargar esta vista. Recarga la página; si sigue pasando, "
        "avisa al equipo.",
        detalle=f"{type(e).__name__}: {e}",
    )
    logging.getLogger(__name__).exception("Error en la vista")

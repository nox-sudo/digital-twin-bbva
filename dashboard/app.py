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

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard import acceso  # noqa: E402

st.set_page_config(page_title="Gemelo Digital Financiero", layout="wide")

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
st.sidebar.caption(f"Sesión: {usuario.usuario} ({usuario.rol})")
if st.sidebar.button("Cerrar sesión"):
    acceso.cerrar_sesion()
    st.rerun()
st.sidebar.caption(
    "Gemelo Digital Financiero · Path Data Engineering BBVA × Tecmilenio"
)
navegacion.run()

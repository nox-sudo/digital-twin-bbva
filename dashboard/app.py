"""
dashboard/app.py

Punto de entrada del dashboard del Gemelo Digital Financiero. Tres
vistas sobre la misma capa Gold:

  - Portafolio: vista ejecutiva, donde se concentra el riesgo.
  - Cliente: vista 360 de una persona.
  - Observabilidad: estado de las corridas y coherencia de Gold.

Solo lee: DuckDB en modo solo lectura y los logs JSON de cada corrida.
No necesita Spark ni ningun servicio de Docker Compose.

Uso, desde la raiz del repositorio:
    uv run --group dashboard streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

st.set_page_config(page_title="Gemelo Digital Financiero", layout="wide")

PAGINAS = Path(__file__).resolve().parent / "paginas"
portafolio = st.Page(PAGINAS / "portafolio.py", title="Portafolio", default=True)
cliente = st.Page(PAGINAS / "cliente.py", title="Cliente")
observabilidad = st.Page(PAGINAS / "observabilidad.py", title="Observabilidad")

# La tabla de clientes a revisar del portafolio salta a la vista de
# cliente; guarda aqui la referencia a la pagina para no duplicar rutas.
st.session_state["pagina_cliente"] = cliente

navegacion = st.navigation(
    {"Negocio": [portafolio, cliente], "Operación": [observabilidad]}
)
st.sidebar.caption(
    "Gemelo Digital Financiero · Path Data Engineering BBVA × Tecmilenio"
)
navegacion.run()

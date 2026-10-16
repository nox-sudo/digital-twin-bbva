"""
dashboard/comun.py

Piezas compartidas por las tres paginas: configuracion, carga de datos
con cache, formato de cifras y la paleta de graficas.

La cache se invalida sola cuando cambia Gold: cada funcion recibe la
fecha de modificacion del archivo DuckDB como argumento, asi que una
corrida nueva del pipeline produce una llave nueva y el dashboard
muestra los datos frescos sin reiniciarlo.
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from dashboard import datos  # noqa: E402

# Paleta. Dos series (entradas y salidas) usan los dos primeros colores
# categoricos; las bandas de riesgo usan colores de estado, siempre con su
# etiqueta escrita al lado, nunca solo el color. Una sola serie usa el azul.
AZUL = "#2a78d6"
NARANJA = "#eb6834"
AQUA = "#1baf7a"
COLOR_BANDA = {
    "bajo": "#0ca30c",
    "medio": "#fab219",
    "alto": "#d03b3b",
    "sin score": "#9a9a96",
}
ORDEN_BANDAS = ["bajo", "medio", "alto", "sin score"]


@st.cache_data
def config() -> dict:
    cfg = datos.cargar_config(str(RAIZ / "config" / "dashboard.yaml"))
    cfg["gold_duckdb"] = str(RAIZ / cfg["gold_duckdb"])
    cfg["logs"] = str(RAIZ / cfg["logs"])
    return cfg


def version_gold() -> float:
    ruta = Path(config()["gold_duckdb"])
    return ruta.stat().st_mtime if ruta.exists() else 0.0


@st.cache_data
def portafolio(version: float) -> pd.DataFrame:
    cfg = config()
    return datos.portafolio(cfg["gold_duckdb"], cfg["bandas_riesgo"])


@st.cache_data
def flujo(version: float, cliente_id: str | None = None) -> pd.DataFrame:
    return datos.flujo_mensual(config()["gold_duckdb"], cliente_id)


@st.cache_data
def salidas_categoria(version: float, cliente_id: str) -> pd.DataFrame:
    return datos.salidas_por_categoria(config()["gold_duckdb"], cliente_id)


def gold_disponible() -> bool:
    """Si Gold no existe, cada pagina muestra como generarlo en vez de un
    error de Python."""
    if Path(config()["gold_duckdb"]).exists():
        return True
    st.warning(
        "Todavía no hay datos en Gold. Corre el pipeline completo "
        "(`./demo.sh` o `uv run python main.py --help`) y recarga esta página."
    )
    return False


def pesos(valor: float | None) -> str:
    if valor is None or pd.isna(valor):
        return "sin dato"
    signo = "-" if valor < 0 else ""
    return f"{signo}${abs(valor):,.0f}"


def porcentaje(valor: float | None, decimales: int = 0) -> str:
    if valor is None or pd.isna(valor):
        return "sin dato"
    return f"{valor * 100:.{decimales}f} %"


def nombre_mes(mes: str) -> str:
    """'2026-03' -> 'mar 2026', para ejes legibles."""
    meses = "ene feb mar abr may jun jul ago sep oct nov dic".split()
    anio, numero = mes.split("-")
    return f"{meses[int(numero) - 1]} {anio}"


def fecha(valor) -> str:
    """Fecha de corte legible: '30 sep 2026'."""
    if valor is None or pd.isna(valor):
        return "sin fecha"
    valor = pd.Timestamp(valor)
    return f"{valor.day} {nombre_mes(f'{valor.year}-{valor.month:02d}')}"

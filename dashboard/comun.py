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

# Nombres para mostrar de las categorias de los datos (generador y
# gold_movimientos_mensuales), que vienen sin acentos y con guion bajo.
NOMBRE_CATEGORIA = {
    "comida": "Comida",
    "transporte": "Transporte",
    "entretenimiento": "Entretenimiento",
    "servicios": "Servicios",
    "salud": "Salud",
    "educacion": "Educación",
    "hogar": "Hogar",
    "ropa": "Ropa",
    "suscripciones": "Suscripciones",
    "otros": "Otros",
    "pago_deuda": "Pago de deuda",
    "efectivo": "Retiro en efectivo",
    "transferencia_personal": "Transferencias personales",
    "ingreso": "Ingreso",
}


def nombre_categoria(valor) -> str:
    """Nombre legible de una categoria; una que no este en la tabla se
    muestra con espacios en vez de guiones bajos."""
    if valor is None or pd.isna(valor):
        return "Sin categoría"
    return NOMBRE_CATEGORIA.get(valor, str(valor).replace("_", " ").capitalize())


@st.cache_data
def config() -> dict:
    cfg = datos.cargar_config(str(RAIZ / "config" / "dashboard.yaml"))
    cfg["gold_duckdb"] = str(RAIZ / cfg["gold_duckdb"])
    cfg["logs"] = str(RAIZ / cfg["logs"])
    cfg["deriva_psi"] = str(RAIZ / cfg["deriva_psi"])
    cfg["resumen_portafolio"] = str(RAIZ / cfg["resumen_portafolio"])
    cfg["monitoreo_psi"] = str(RAIZ / cfg["monitoreo_psi"])
    # Ventana de las features de flujo (config/features.yaml), para decirla en
    # los textos de ayuda. Se lee el YAML directo: kpi_definitions importa
    # Spark y el dashboard no lo necesita.
    cfg["ventana_meses"] = datos.cargar_config(str(RAIZ / "config" / "features.yaml"))[
        "ventana_meses"
    ]
    # Tasa media de impago a la que se calibra la etiqueta en cada corrida,
    # para explicar por que la probabilidad media no sigue al deterioro.
    cfg["tasa_objetivo"] = datos.cargar_config(
        str(RAIZ / "config" / "etiquetas_impago.yaml")
    )["tasa_objetivo"]
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
    cfg = config()
    return datos.salidas_por_categoria(
        cfg["gold_duckdb"], cliente_id, cfg["ventana_meses"]
    )


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

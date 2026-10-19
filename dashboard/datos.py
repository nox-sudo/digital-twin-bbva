"""
dashboard/datos.py

Capa de datos del dashboard. Todas las consultas viven aqui y ninguna
importa Streamlit, por dos razones:

  - se prueban con pytest sin levantar la app (tests/test_dashboard_datos.py);
  - el simulador y el asistente pueden reutilizar las mismas consultas.

Fuentes, ambas de solo lectura:

  - Gold (DuckDB): gold_kpis, gold_features_cliente y
    gold_movimientos_mensuales. Se abre con read_only=True: el dashboard
    nunca puede modificar la capa que consume.
  - data/logs/*.json: un archivo por corrida de cada capa, que ya escriben
    ingest_bronze.py y PipelineRunLogger. Alcanza para la vista de
    observabilidad sin leer Delta ni levantar Spark.
"""

import json
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pandas as pd
import yaml

TIPOS_ENTRADA = ["nomina", "p2p_recibido"]


def cargar_config(ruta: str = "config/dashboard.yaml") -> dict:
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


@contextmanager
def conexion_gold(ruta_duckdb: str):
    if not Path(ruta_duckdb).exists():
        raise FileNotFoundError(
            f"No existe {ruta_duckdb}. Corre el pipeline hasta Gold "
            "(python main.py gold, o demo.sh) antes de abrir el dashboard."
        )
    con = duckdb.connect(ruta_duckdb, read_only=True)
    try:
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Portafolio y cliente
# ---------------------------------------------------------------------------


def asignar_banda(probabilidad: pd.Series, bandas: list[dict]) -> pd.Series:
    """Banda de riesgo por cliente. Las bandas vienen de
    config/dashboard.yaml, ordenadas por su limite inferior; un cliente
    sin probabilidad (modelo aun no corrido) queda como "sin score"."""
    limites = sorted(bandas, key=lambda b: b["desde"])
    resultado = pd.Series("sin score", index=probabilidad.index, dtype="object")
    for banda in limites:
        resultado[probabilidad >= banda["desde"]] = banda["nombre"]
    return resultado


def portafolio(ruta_duckdb: str, bandas: list[dict]) -> pd.DataFrame:
    """Una fila por cliente: features de Gold, probabilidad de impago y
    banda de riesgo. Es la tabla base de la vista ejecutiva."""
    with conexion_gold(ruta_duckdb) as con:
        df = con.execute("""
            SELECT f.*, k.valor_numerico AS probabilidad_impago
            FROM gold_features_cliente f
            LEFT JOIN gold_kpis k
              ON k.cliente_id = f.cliente_id AND k.kpi_id = 'probabilidad_impago'
            ORDER BY f.cliente_id
            """).fetchdf()
    df["banda_riesgo"] = asignar_banda(df["probabilidad_impago"], bandas)
    return df


def resumen_portafolio(df: pd.DataFrame) -> dict:
    """Cifras de cabecera. Se calculan aqui, no en la pagina, para que la
    misma definicion la usen la demo y las pruebas."""
    con_score = df["probabilidad_impago"].notna()
    return {
        "clientes": len(df),
        "ingreso_mediano": float(df["ingreso_mensual_promedio"].median()),
        "capacidad_ahorro_mediana": float(df["capacidad_ahorro"].median()),
        "pct_ahorro_negativo": float((df["capacidad_ahorro"] < 0).mean()),
        "probabilidad_media": (
            float(df.loc[con_score, "probabilidad_impago"].mean())
            if con_score.any()
            else None
        ),
        "clientes_sin_score": int((~con_score).sum()),
        "fecha_corte": df["fecha_corte"].iloc[0] if len(df) else None,
    }


def flujo_mensual(ruta_duckdb: str, cliente_id: str | None = None) -> pd.DataFrame:
    """Entradas, salidas y neto por mes. Sin cliente_id, del portafolio
    completo. Las salidas se devuelven en positivo para graficarlas lado
    a lado con las entradas; el neto conserva su signo."""
    filtro = "WHERE cliente_id = ?" if cliente_id else ""
    parametros = [cliente_id] if cliente_id else []
    with conexion_gold(ruta_duckdb) as con:
        return con.execute(
            f"""
            SELECT
                mes,
                SUM(CASE WHEN monto_total > 0 THEN monto_total ELSE 0 END) AS entradas,
                -SUM(CASE WHEN monto_total < 0 THEN monto_total ELSE 0 END) AS salidas,
                SUM(monto_total) AS neto
            FROM gold_movimientos_mensuales
            {filtro}
            GROUP BY mes
            ORDER BY mes
            """,
            parametros,
        ).fetchdf()


def salidas_por_categoria(ruta_duckdb: str, cliente_id: str) -> pd.DataFrame:
    """A donde se va el dinero de un cliente en todo el periodo: todas las
    salidas (compras, pagos recurrentes, retiros, envios P2P) por
    categoria, con su proporcion del total."""
    with conexion_gold(ruta_duckdb) as con:
        df = con.execute(
            """
            SELECT categoria, -SUM(monto_total) AS total
            FROM gold_movimientos_mensuales
            WHERE cliente_id = ? AND monto_total < 0
            GROUP BY categoria
            ORDER BY total DESC
            """,
            [cliente_id],
        ).fetchdf()
    df["proporcion"] = df["total"] / df["total"].sum() if len(df) else []
    return df


def percentil_en_portafolio(df: pd.DataFrame, columna: str, valor: float) -> float:
    """Que proporcion del portafolio tiene un valor menor o igual. Sirve
    para leer un numero suelto ("ahorra 4,000") con contexto ("mas que el
    62 % de los clientes")."""
    serie = df[columna].dropna()
    if serie.empty or pd.isna(valor):
        return float("nan")
    return float((serie <= valor).mean())


# ---------------------------------------------------------------------------
# Observabilidad
# ---------------------------------------------------------------------------


def _capa_de(nombre_archivo: str) -> str:
    return nombre_archivo.split("_")[0]


def corridas(ruta_logs: str) -> pd.DataFrame:
    """Una fila por corrida registrada en data/logs/. Normaliza las dos
    formas de log que existen hoy: la de Bronze (duracion_segundos,
    estatus) y la de PipelineRunLogger (duracion_total_segundos, status)."""
    filas = []
    for archivo in sorted(Path(ruta_logs).glob("*.json")):
        try:
            log = json.loads(archivo.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            filas.append(
                {
                    "capa": _capa_de(archivo.name),
                    "archivo": archivo.name,
                    "estatus": "LOG ILEGIBLE",
                    "detalle": f"No se pudo leer {archivo.name}",
                }
            )
            continue
        filas.append(
            {
                "capa": log.get("layer", _capa_de(archivo.name)),
                "archivo": archivo.name,
                "inicio": pd.to_datetime(log.get("inicio")),
                "duracion_segundos": log.get(
                    "duracion_total_segundos", log.get("duracion_segundos")
                ),
                # Un status largo ("FAILED: <error>") se reduce a su primera
                # palabra para agrupar; el detalle queda en "detalle".
                "estatus": str(log.get("status", log.get("estatus", "?"))).split(":")[
                    0
                ],
                "detalle": str(log.get("status", log.get("estatus", ""))),
            }
        )
    columnas = ["capa", "archivo", "inicio", "duracion_segundos", "estatus", "detalle"]
    # Orden por nombre de archivo: lleva la fecha de la corrida y, a
    # diferencia de "inicio", existe aunque el log este danado. Asi un log
    # ilegible reciente cuenta como la ultima corrida de su capa.
    return pd.DataFrame(filas, columns=columnas).sort_values(["capa", "archivo"])


def entidades_ultima_corrida(ruta_logs: str, capa: str) -> pd.DataFrame:
    """Detalle por entidad de la corrida mas reciente de una capa
    (silver o gold), tal como lo registra PipelineRunLogger."""
    archivos = sorted(Path(ruta_logs).glob(f"{capa}_run_*.json"))
    if not archivos:
        return pd.DataFrame()
    log = json.loads(archivos[-1].read_text(encoding="utf-8"))
    filas = []
    for entidad, datos in log.get("entities", {}).items():
        fila = {"entidad": entidad}
        for clave, valor in datos.items():
            if clave == "violaciones_por_regla":
                fila["reglas_violadas"] = (
                    ", ".join(f"{r} ({n})" for r, n in valor.items()) if valor else ""
                )
            else:
                fila[clave] = valor
        filas.append(fila)
    return pd.DataFrame(filas)


def consistencia_gold(ruta_duckdb: str) -> pd.DataFrame:
    """Comprobaciones de contrato entre las tablas de Gold: que todas
    hablen de los mismos clientes y de la misma fecha de corte. Cada fila
    es una comprobacion con su resultado, para mostrarla tal cual."""
    with conexion_gold(ruta_duckdb) as con:
        tablas = {t for (t,) in con.execute("SHOW TABLES").fetchall()}

        def contar(sql: str) -> int:
            return con.execute(sql).fetchone()[0]

        clientes_features = contar("SELECT COUNT(*) FROM gold_features_cliente")
        chequeos = [
            (
                "Clientes con KPIs = clientes en el feature store",
                contar("SELECT COUNT(DISTINCT cliente_id) FROM gold_kpis"),
                clientes_features,
            ),
            (
                "Clientes con probabilidad de impago",
                contar(
                    "SELECT COUNT(*) FROM gold_kpis WHERE kpi_id = 'probabilidad_impago' "
                    "AND valor_numerico IS NOT NULL"
                ),
                clientes_features,
            ),
            (
                "Fechas de corte distintas en el feature store",
                contar("SELECT COUNT(DISTINCT fecha_corte) FROM gold_features_cliente"),
                1,
            ),
        ]
        if "gold_movimientos_mensuales" in tablas:
            chequeos.append(
                (
                    "Clientes con movimientos mensuales",
                    contar(
                        "SELECT COUNT(DISTINCT cliente_id) FROM gold_movimientos_mensuales"
                    ),
                    clientes_features,
                )
            )
        else:
            chequeos.append(("Tabla gold_movimientos_mensuales", 0, 1))

    return pd.DataFrame(
        [
            {
                "comprobacion": nombre,
                "observado": obs,
                "esperado": esp,
                "ok": obs == esp,
            }
            for nombre, obs, esp in chequeos
        ]
    )


# ---------------------------------------------------------------------------
# Deriva de datos (PSI)
# ---------------------------------------------------------------------------

COLUMNAS_DERIVA = [
    "run_id",
    "fecha_corrida",
    "fecha_corte",
    "referencia_id",
    "variable",
    "tipo",
    "psi",
    "estado",
]


def deriva_psi(ruta_historico: str) -> pd.DataFrame:
    """Historico del monitoreo de deriva (monitor_drift.py): un Parquet por
    corrida en data/calidad/deriva_psi/. Vacio si todavia no hay corridas."""
    if not list(Path(ruta_historico).glob("*.parquet")):
        return pd.DataFrame(columns=COLUMNAS_DERIVA)
    patron = str(Path(ruta_historico) / "*.parquet")
    return duckdb.sql(
        f"SELECT {', '.join(COLUMNAS_DERIVA)} FROM read_parquet('{patron}')"
    ).df()


def tendencia_psi(historico: pd.DataFrame) -> pd.DataFrame:
    """Una fila por variable y fecha de corte, contra la referencia activa.

    La referencia activa es la de la corrida mas reciente: las corridas
    contra una referencia anterior miden contra otra linea base y no se
    mezclan en la misma tendencia. Si una fecha de corte se midio varias
    veces (por ejemplo, el DAG y demo.sh pipeline sobre la misma entrega),
    queda la ultima corrida."""
    if historico.empty:
        return historico
    ultima = historico.sort_values("fecha_corrida").iloc[-1]
    activa = historico[historico["referencia_id"] == ultima["referencia_id"]]
    ultimas_corridas = (
        activa.sort_values("fecha_corrida").groupby("fecha_corte")["run_id"].last()
    )
    return (
        activa[activa["run_id"].isin(ultimas_corridas)]
        .sort_values(["variable", "fecha_corte"])
        .reset_index(drop=True)
    )

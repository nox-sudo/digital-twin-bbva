"""
src/gold/resumen_portafolio.py

Cifras de cabecera del portafolio (las de la vista Portafolio del dashboard)
y su historico por fecha de corte.

Gold se reemplaza en cada corrida, asi que el resumen de un corte anterior
deja de existir cuando llega una entrega nueva. predict_risk.py, el paso que
termina el score, agrega una fila por corrida a data/calidad/resumen_portafolio/
(un Parquet por corrida, esquema fijo, junto al registro de calidad y al
historico del PSI; entra en demo.sh respaldar). El dashboard compara el corte
actual contra el mas antiguo del historico, la sesion base.

Una sola definicion: resumen() la usan el pipeline al escribir el historico y
el dashboard al mostrar el corte actual, asi que la comparacion es entre la
misma medida. Sin Spark.
"""

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ESQUEMA_HISTORICO = pa.schema(
    [
        pa.field("fecha_corrida", pa.timestamp("s")),
        pa.field("fecha_corte", pa.date32()),
        pa.field("clientes", pa.int64()),
        pa.field("ingreso_mediano", pa.float64()),
        pa.field("pct_ahorro_negativo", pa.float64()),
        pa.field("probabilidad_media", pa.float64()),
    ]
)


def resumen(df: pd.DataFrame) -> dict:
    """Cifras de cabecera de una tabla con una fila por cliente:
    ingreso_mensual_promedio, capacidad_ahorro, probabilidad_impago y
    fecha_corte (gold_features_cliente mas el score)."""
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


def escribir_historico(
    cifras: dict, ruta: str | Path, ahora: datetime | None = None
) -> Path:
    """Agrega una corrida al historico: un Parquet por corrida, escrito a un
    temporal y renombrado para que un lector nunca vea un archivo a medias."""
    ahora = ahora or datetime.now()
    carpeta = Path(ruta)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"resumen_{ahora:%Y%m%d_%H%M%S_%f}.parquet"
    fila = pd.DataFrame(
        [
            {
                "fecha_corrida": pd.Timestamp(ahora).floor("s"),
                "fecha_corte": pd.Timestamp(cifras["fecha_corte"]).date(),
                "clientes": cifras["clientes"],
                "ingreso_mediano": cifras["ingreso_mediano"],
                "pct_ahorro_negativo": cifras["pct_ahorro_negativo"],
                "probabilidad_media": cifras["probabilidad_media"],
            }
        ]
    )
    tabla = pa.Table.from_pandas(fila, schema=ESQUEMA_HISTORICO, preserve_index=False)
    temporal = destino.with_suffix(".parquet.tmp")
    pq.write_table(tabla, temporal)
    os.replace(temporal, destino)
    return destino


def cargar_historico(ruta: str | Path) -> pd.DataFrame:
    """Una fila por fecha de corte (la ultima corrida de cada corte), de la
    mas antigua a la mas reciente. Vacio si todavia no hay corridas."""
    archivos = sorted(Path(ruta).glob("*.parquet"))
    if not archivos:
        return pd.DataFrame(columns=ESQUEMA_HISTORICO.names)
    historico = pd.concat(pd.read_parquet(a) for a in archivos)
    return (
        historico.sort_values("fecha_corrida")
        .groupby("fecha_corte", as_index=False)
        .last()
        .sort_values("fecha_corte")
        .reset_index(drop=True)
    )

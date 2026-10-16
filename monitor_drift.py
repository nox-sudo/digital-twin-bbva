"""
monitor_drift.py

Monitoreo de deriva de datos (data drift) con PSI: compara la distribucion
actual de cada feature numerica del modelo, y del score, contra la
referencia congelada del modelo base (src/monitoreo/referencia.py).

Es el ultimo paso del DAG. Si todavia no hay referencia (primera corrida,
o despues de "demo.sh limpiar todo"), la crea con el modelo recien
entrenado y su poblacion de entrenamiento; esa corrida da PSI 0 y queda
como punto de partida de la tendencia. Las corridas siguientes NO tocan la
referencia aunque el DAG reentrene el modelo: el PSI mide la deriva
acumulada desde la linea base. Para promover una linea base nueva:

    python monitor_drift.py --nueva-referencia

que archiva la anterior (no la borra) y crea otra con el modelo actual.

Cada corrida agrega un Parquet a data/calidad/deriva_psi/ (fecha de
corrida, variable, psi, estado). Una variable en estado "reentrenar" se
registra como alerta en el log, pero el pipeline no falla: el DAG ya
reentrena cada mes, asi que la alerta es informativa (ver
docs/monitoreo-deriva.md). No necesita Spark.

Uso:
    python monitor_drift.py --gold data/gold/kpis.duckdb \
        --model models/risk_model.joblib --config config/monitoreo_psi.yaml
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.logging_utils import PipelineRunLogger  # noqa: E402
from src.gold.etiquetas_gold import cargar_etiquetas  # noqa: E402
from src.gold.risk_features import (  # noqa: E402
    cargar_features,
    preparar_para_modelo,
)
from src.monitoreo.psi import (  # noqa: E402
    REENTRENAR,
    ConfigPSI,
    cargar_config,
    clasificar,
    proporciones,
    psi,
)
from src.monitoreo.referencia import (  # noqa: E402
    cargar_activa,
    crear_referencia,
    id_activa,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ESQUEMA_HISTORICO = pa.schema(
    [
        pa.field("run_id", pa.string()),
        pa.field("fecha_corrida", pa.timestamp("s")),
        pa.field("fecha_corte", pa.date32()),
        pa.field("referencia_id", pa.string()),
        pa.field("variable", pa.string()),
        pa.field("tipo", pa.string()),
        pa.field("psi", pa.float64()),
        pa.field("estado", pa.string()),
        pa.field("n_referencia", pa.int64()),
        pa.field("n_actual", pa.int64()),
    ]
)


def _fecha_corte(features: pd.DataFrame):
    if "fecha_corte" not in features.columns or features["fecha_corte"].isna().all():
        return None
    return pd.Timestamp(features["fecha_corte"].dropna().iloc[0]).date()


def _score(modelo, features: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Matriz alineada a las columnas del modelo y probabilidad de impago."""
    matriz = preparar_para_modelo(features, columnas_modelo=modelo.feature_names_in_)
    return matriz, pd.Series(modelo.predict_proba(matriz)[:, 1], index=matriz.index)


def crear_desde_modelo_actual(
    cfg: ConfigPSI, features: pd.DataFrame, gold: str, ruta_modelo: str
) -> str:
    """Referencia con el modelo actual y su poblacion de entrenamiento: los
    clientes con features y etiqueta, el mismo cruce que hace train_model.py."""
    modelo = joblib.load(ruta_modelo)
    etiquetas = cargar_etiquetas(gold)
    entrenamiento = features[features.index.isin(etiquetas["cliente_id"])]
    if entrenamiento.empty:
        raise RuntimeError("Ningun cliente de gold_features_cliente tiene etiqueta")
    matriz, score = _score(modelo, entrenamiento)
    return crear_referencia(
        cfg.ruta_referencias,
        matriz,
        score,
        ruta_modelo,
        cfg.bins,
        _fecha_corte(entrenamiento),
    )


def medir(
    cfg: ConfigPSI, referencia: dict, modelo_base, features: pd.DataFrame
) -> pd.DataFrame:
    """PSI y estado por variable, con los cortes guardados en la referencia."""
    matriz, score = _score(modelo_base, features)
    filas = []
    for variable, ref in referencia["variables"].items():
        valor = psi(
            ref["proporciones"],
            proporciones(matriz[variable], ref["cortes"]),
            cfg.epsilon,
        )
        filas.append({"variable": variable, "tipo": "feature", "psi": valor})
    ref_score = referencia["score"]
    valor = psi(
        ref_score["proporciones"], proporciones(score, ref_score["cortes"]), cfg.epsilon
    )
    filas.append({"variable": ref_score["variable"], "tipo": "score", "psi": valor})

    resultados = pd.DataFrame(filas)
    resultados["estado"] = resultados["psi"].map(lambda v: clasificar(v, cfg))
    return resultados


def escribir_historico(resultados: pd.DataFrame, ruta: str | Path, run_id: str) -> Path:
    """Agrega la corrida al historico: un Parquet por corrida con esquema
    fijo, escrito a un temporal y renombrado para que un lector nunca vea un
    archivo a medio escribir."""
    carpeta = Path(ruta)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{run_id}.parquet"
    if destino.exists():
        raise FileExistsError(f"Ya existe {destino}; el historico no se sobrescribe")
    tabla = pa.Table.from_pandas(
        resultados[ESQUEMA_HISTORICO.names],
        schema=ESQUEMA_HISTORICO,
        preserve_index=False,
    )
    temporal = destino.with_suffix(".parquet.tmp")
    pq.write_table(tabla, temporal)
    os.replace(temporal, destino)
    return destino


def main():
    parser = argparse.ArgumentParser(description="Monitoreo de deriva de datos con PSI")
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument("--model", default="models/risk_model.joblib")
    parser.add_argument("--config", default="config/monitoreo_psi.yaml")
    parser.add_argument("--logs", default="data/logs")
    parser.add_argument(
        "--nueva-referencia",
        action="store_true",
        help="Archiva la referencia activa y crea otra con el modelo actual",
    )
    args = parser.parse_args()

    cfg = cargar_config(args.config)
    run_logger = PipelineRunLogger(layer="monitoreo", log_dir=args.logs)
    status = "SUCCESS"
    try:
        features = cargar_features(args.gold)

        anterior = id_activa(cfg.ruta_referencias)
        if anterior is None or args.nueva_referencia:
            run_logger.start_entity("referencia")
            nueva = crear_desde_modelo_actual(cfg, features, args.gold, args.model)
            run_logger.end_entity("referencia", creada=nueva, archivada=anterior or "")
            logger.info(
                "Referencia %s creada con %s%s",
                nueva,
                args.model,
                f"; la anterior ({anterior}) queda archivada" if anterior else "",
            )

        referencia, ruta_modelo_base = cargar_activa(cfg.ruta_referencias)
        modelo_base = joblib.load(ruta_modelo_base)

        run_logger.start_entity("deriva_psi")
        ahora = datetime.now()
        run_id = f"monitoreo_{ahora:%Y%m%d_%H%M%S_%f}"
        resultados = medir(cfg, referencia, modelo_base, features)
        resultados["run_id"] = run_id
        resultados["fecha_corrida"] = pd.Timestamp(ahora).floor("s")
        resultados["fecha_corte"] = _fecha_corte(features)
        resultados["referencia_id"] = referencia["id"]
        resultados["n_referencia"] = referencia["n_clientes"]
        resultados["n_actual"] = len(features)
        destino = escribir_historico(resultados, cfg.ruta_historico, run_id)

        por_estado = resultados["estado"].value_counts()
        alertas = resultados[resultados["estado"] == REENTRENAR]
        score = resultados[resultados["tipo"] == "score"].iloc[0]
        run_logger.end_entity(
            "deriva_psi",
            referencia=referencia["id"],
            variables=len(resultados),
            estables=int(por_estado.get("estable", 0)),
            vigilar=int(por_estado.get("vigilar", 0)),
            reentrenar=int(por_estado.get(REENTRENAR, 0)),
            psi_score=round(float(score["psi"]), 4),
            alertas=alertas["variable"].tolist(),
        )
        for _, fila in alertas.iterrows():
            logger.warning(
                "ALERTA deriva: %s PSI=%.3f > %.2f contra la referencia %s "
                "(estado reentrenar). El pipeline continua.",
                fila["variable"],
                fila["psi"],
                cfg.umbral_reentrenar,
                referencia["id"],
            )
        logger.info("Historico de deriva agregado en %s", destino)
        print(f"\n=== PSI contra la referencia {referencia['id']} ===")
        print(
            resultados.sort_values("psi", ascending=False)[
                ["variable", "tipo", "psi", "estado"]
            ].to_string(index=False, float_format=lambda v: f"{v:.4f}")
        )
    except Exception as e:
        status = f"FAILED: {e}"
        logger.exception("Fallo el monitoreo de deriva")
        raise
    finally:
        run_logger.write(status=status)


if __name__ == "__main__":
    main()

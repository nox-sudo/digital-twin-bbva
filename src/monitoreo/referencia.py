"""
src/monitoreo/referencia.py

Referencia congelada del monitoreo de deriva: la linea base contra la que
se mide el PSI de cada corrida. Se crea una vez, con el modelo vigente y
su poblacion de entrenamiento, y se conserva aunque el DAG reentrene el
modelo cada mes. Asi el PSI mide la deriva acumulada desde la linea base,
no solo el cambio respecto al mes anterior.

Estructura en disco (rutas.referencias en config/monitoreo_psi.yaml):

    models/referencia_psi/
        activa.json                 cual referencia esta activa y cual habia antes
        ref_20261016_130501/
            referencia.json         cortes y proporciones de entrenamiento
            modelo_base.joblib      copia del modelo con el que se creo
        ref_20261201_090000/        otra referencia (la anterior queda archivada)

Una referencia nunca se sobrescribe: crear otra (monitor_drift.py
--nueva-referencia) agrega una carpeta con su fecha y solo mueve el
marcador de activa.json. referencia.json no guarda datos de clientes,
solo cortes y proporciones agregadas.
"""

import hashlib
import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.gold.risk_features import PREFIJO_ONEHOT
from src.monitoreo.psi import calcular_cortes, proporciones

ARCHIVO_ACTIVA = "activa.json"
ARCHIVO_REFERENCIA = "referencia.json"
ARCHIVO_MODELO = "modelo_base.joblib"
VARIABLE_SCORE = "probabilidad_impago"


def variables_numericas(columnas_modelo) -> list[str]:
    """Features del modelo sobre las que se mide PSI: todas menos las
    columnas one-hot de la categoria de gasto, que son la codificacion de
    una sola variable categorica y no una distribucion numerica."""
    return [c for c in columnas_modelo if not c.startswith(f"{PREFIJO_ONEHOT}_")]


def _sha256(ruta: Path) -> str:
    digest = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _escribir_json_atomico(contenido: dict, ruta: Path) -> None:
    """Archivo temporal y os.replace: quien lea activa.json ve el marcador
    anterior completo o el nuevo completo, nunca uno a medio escribir."""
    temporal = ruta.with_suffix(".json.tmp")
    temporal.write_text(
        json.dumps(contenido, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(temporal, ruta)


def id_activa(raiz: str | Path) -> str | None:
    marcador = Path(raiz) / ARCHIVO_ACTIVA
    if not marcador.exists():
        return None
    return json.loads(marcador.read_text(encoding="utf-8"))["activa"]


def crear_referencia(
    raiz: str | Path,
    matriz: pd.DataFrame,
    score,
    ruta_modelo: str | Path,
    bins: int,
    fecha_corte: date | None,
    ahora: datetime | None = None,
) -> str:
    """Crea una referencia nueva y la marca como activa. Devuelve su id.

    matriz son las features de la poblacion de entrenamiento, ya alineadas
    a las columnas del modelo; score, la probabilidad que ese modelo les
    asigna. Los cortes salen de aqui y no se vuelven a calcular."""
    raiz = Path(raiz)
    ahora = ahora or datetime.now()
    ref_id = f"ref_{ahora:%Y%m%d_%H%M%S}"
    carpeta = raiz / ref_id
    if carpeta.exists():
        raise FileExistsError(f"Ya existe la referencia {carpeta}; no se sobrescribe")

    variables = {}
    for variable in variables_numericas(matriz.columns):
        cortes = calcular_cortes(matriz[variable], bins)
        variables[variable] = {
            "cortes": cortes,
            "proporciones": proporciones(matriz[variable], cortes).tolist(),
        }
    cortes_score = calcular_cortes(score, bins)

    carpeta.mkdir(parents=True)
    copia_modelo = carpeta / ARCHIVO_MODELO
    shutil.copy2(ruta_modelo, copia_modelo)
    contenido = {
        "id": ref_id,
        "creada": ahora.isoformat(timespec="seconds"),
        "fecha_corte": fecha_corte.isoformat() if fecha_corte else None,
        "n_clientes": int(len(matriz)),
        "bins": bins,
        "modelo_sha256": _sha256(copia_modelo),
        "variables": variables,
        "score": {
            "variable": VARIABLE_SCORE,
            "cortes": cortes_score,
            "proporciones": proporciones(score, cortes_score).tolist(),
        },
    }
    (carpeta / ARCHIVO_REFERENCIA).write_text(
        json.dumps(contenido, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # El marcador se mueve al final: si algo falla antes, la carpeta queda
    # incompleta pero inactiva, y la referencia anterior sigue vigente.
    _escribir_json_atomico(
        {
            "activa": ref_id,
            "desde": ahora.isoformat(timespec="seconds"),
            "anterior": id_activa(raiz),
        },
        raiz / ARCHIVO_ACTIVA,
    )
    return ref_id


def cargar_activa(raiz: str | Path) -> tuple[dict, Path] | None:
    """Contenido de la referencia activa y ruta de su modelo base, o None si
    todavia no hay ninguna. Falla si el modelo base no es el que se guardo
    al crearla: medir con otro modelo mezclaria cambio de modelo con
    cambio de datos."""
    ref_id = id_activa(raiz)
    if ref_id is None:
        return None
    carpeta = Path(raiz) / ref_id
    contenido = json.loads((carpeta / ARCHIVO_REFERENCIA).read_text(encoding="utf-8"))
    ruta_modelo = carpeta / ARCHIVO_MODELO
    if _sha256(ruta_modelo) != contenido["modelo_sha256"]:
        raise ValueError(
            f"{ruta_modelo} no coincide con el hash guardado en {ARCHIVO_REFERENCIA}"
        )
    return contenido, ruta_modelo

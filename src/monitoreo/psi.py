"""
src/monitoreo/psi.py

Indice de estabilidad poblacional (PSI): cuanto se movio la distribucion
de una variable respecto a una distribucion de referencia.

    PSI = suma sobre intervalos de (actual - referencia) * ln(actual / referencia)

Los intervalos salen de la referencia (deciles de la distribucion de
entrenamiento) y se guardan con ella. Una corrida nueva solo cuenta en que
intervalo cae cada valor; nunca recalcula cortes con los datos nuevos: si
lo hiciera, cada intervalo volveria a tener ~10 % y el PSI no detectaria
un desplazamiento de toda la distribucion.

Funciones puras (numpy), sin Spark ni XGBoost, para poder probarlas solas.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

ESTABLE = "estable"
VIGILAR = "vigilar"
REENTRENAR = "reentrenar"
# Variables que derivan por el paso del tiempo (config variables_temporales):
# su PSI se reporta pero no se clasifica ni alerta.
TEMPORAL = "temporal"


@dataclass(frozen=True)
class ConfigPSI:
    bins: int
    epsilon: float
    umbral_vigilar: float
    umbral_reentrenar: float
    ruta_referencias: str
    ruta_historico: str
    variables_temporales: tuple[str, ...] = ()


def cargar_config(ruta: str | Path = "config/monitoreo_psi.yaml") -> ConfigPSI:
    """Lee y valida la configuracion. Falla con un mensaje claro si falta una
    llave o un valor no tiene sentido, en vez de clasificar con umbrales
    invertidos o un epsilon que domine el calculo."""
    with open(ruta, encoding="utf-8") as f:
        crudo = yaml.safe_load(f) or {}

    try:
        cfg = ConfigPSI(
            bins=int(crudo["bins"]),
            epsilon=float(crudo["epsilon"]),
            umbral_vigilar=float(crudo["umbrales"]["vigilar"]),
            umbral_reentrenar=float(crudo["umbrales"]["reentrenar"]),
            ruta_referencias=str(crudo["rutas"]["referencias"]),
            ruta_historico=str(crudo["rutas"]["historico"]),
            variables_temporales=tuple(crudo.get("variables_temporales") or ()),
        )
    except KeyError as faltante:
        raise ValueError(f"{ruta}: falta la llave {faltante}") from None

    if not all(isinstance(v, str) and v for v in cfg.variables_temporales):
        raise ValueError(f"{ruta}: variables_temporales debe ser una lista de nombres")

    if cfg.bins < 2:
        raise ValueError(f"{ruta}: bins debe ser >= 2")
    for nombre, valor in (
        ("epsilon", cfg.epsilon),
        ("umbrales.vigilar", cfg.umbral_vigilar),
        ("umbrales.reentrenar", cfg.umbral_reentrenar),
    ):
        if not math.isfinite(valor):
            raise ValueError(f"{ruta}: {nombre} debe ser un numero finito, no {valor}")
    if not 0.0 < cfg.epsilon < 0.01:
        raise ValueError(f"{ruta}: epsilon debe estar en (0, 0.01)")
    if not 0.0 < cfg.umbral_vigilar < cfg.umbral_reentrenar:
        raise ValueError(
            f"{ruta}: se requiere 0 < umbrales.vigilar < umbrales.reentrenar"
        )
    return cfg


def calcular_cortes(valores, bins: int) -> list[float]:
    """Cortes internos por cuantiles de la distribucion de referencia
    (bins=10: deciles, 9 cortes). Los extremos son implicitos (-inf y +inf),
    asi que un valor actual fuera del rango de entrenamiento cae en el primer
    o el ultimo intervalo en vez de perderse.

    Los cortes repetidos se eliminan: una variable binaria o con pocos
    valores distintos queda con menos intervalos, no con intervalos vacios
    de ancho cero. Los faltantes (NaN) no participan en los cortes; si la
    referencia solo tiene faltantes no hay cortes, y el PSI mide solo cuanto
    cambio la proporcion de nulos."""
    x = np.asarray(valores, dtype=float)
    x = x[~np.isnan(x)]
    if x.size == 0:
        return []
    cuantiles = np.linspace(0.0, 1.0, bins + 1)[1:-1]
    return np.unique(np.quantile(x, cuantiles)).tolist()


def proporciones(valores, cortes: list[float]) -> np.ndarray:
    """Fraccion de valores en cada intervalo definido por los cortes, mas
    un intervalo final para los faltantes (NaN): len(cortes) + 2 entradas.

    Intervalos cerrados por la izquierda: un valor igual a un corte cae en
    el intervalo de la derecha, igual en referencia y en actual."""
    x = np.asarray(valores, dtype=float)
    faltantes = np.isnan(x)
    indices = np.searchsorted(
        np.asarray(cortes, dtype=float), x[~faltantes], side="right"
    )
    conteos = np.bincount(indices, minlength=len(cortes) + 1).astype(float)
    conteos = np.append(conteos, float(faltantes.sum()))
    total = conteos.sum()
    if total == 0:
        raise ValueError("No hay valores para calcular proporciones")
    return conteos / total


def psi(referencia, actual, epsilon: float) -> float:
    """PSI entre dos vectores de proporciones sobre los mismos intervalos.
    Un intervalo vacio toma la proporcion epsilon para evitar log(0); si esta
    vacio en ambos, aporta 0."""
    esperadas = np.asarray(referencia, dtype=float)
    observadas = np.asarray(actual, dtype=float)
    if esperadas.shape != observadas.shape:
        raise ValueError(
            f"Intervalos distintos: referencia {esperadas.shape}, actual {observadas.shape}"
        )
    esperadas = np.clip(esperadas, epsilon, None)
    observadas = np.clip(observadas, epsilon, None)
    return float(np.sum((observadas - esperadas) * np.log(observadas / esperadas)))


def clasificar(valor: float, cfg: ConfigPSI) -> str:
    """estable si psi < vigilar; vigilar hasta reentrenar inclusive;
    reentrenar por encima."""
    if not math.isfinite(valor):
        raise ValueError(f"PSI no finito: {valor}")
    if valor < cfg.umbral_vigilar:
        return ESTABLE
    if valor <= cfg.umbral_reentrenar:
        return VIGILAR
    return REENTRENAR

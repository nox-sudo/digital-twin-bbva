"""
src/gold/etiquetas_impago.py

Etiqueta de impago como un evento POSTERIOR a las features, generado con un
modelo latente, en lugar de una regla derivada de las mismas variables con las
que se entrena el modelo (ver docs/technical-debt.md: con la regla anterior el
modelo solo reconstruia la regla, y el ruido de 8 % apenas le ponia un techo al
AUC).

Modelo (todos los parametros viven en config/etiquetas_impago.yaml):

    logit(p) = b0 + b1*ratio_endeudamiento + b2*uso_linea_credito
                  - b3*capacidad_ahorro + b4*z + b5*choque

- Las 3 variables observadas salen del feature store (hasta fecha_observacion),
  winsorizadas en p1/p99 y estandarizadas sobre todos los clientes.
- z ~ N(0, 1) es un rasgo oculto por cliente; choque ~ Bernoulli(1 - (1 - q)^h)
  indica si hubo al menos un choque mensual en los h meses del horizonte.
  Ninguno de los dos esta en el feature store: el modelo no puede verlos.
- b0 se calibra por biseccion para que la probabilidad media sea exactamente la
  tasa objetivo. La etiqueta es y ~ Bernoulli(p).

Ventana temporal. fecha_observacion es la fecha de corte de las features (el
ultimo dia con transacciones en Silver); el impago se simula en los h meses
siguientes. Cada nueva-entrega mueve la fecha de observacion un mes adelante y
las etiquetas se regeneran. Limite: los meses que llegan despues NO reflejan los
choques simulados (el generador de transacciones no sabe del evento), asi que
las etiquetas de una entrega nueva no son hechos historicos, sino una muestra
nueva del mismo proceso.

Clientes sin producto. En el feature store ratio_endeudamiento y
uso_linea_credito valen 0 para quien no tiene prestamo o tarjeta: no es un dato
faltante, es ausencia de exposicion. Se deja en 0 ANTES de winsorizar y
estandarizar, y no se imputa ni se excluye: asi el cliente sin producto recibe
menos riesgo por ese canal que el que lo tiene (no puede caer en impago de un
credito que no tiene), pero conserva el riesgo por capacidad de ahorro, rasgo
oculto y choque. Winsorizar en p1/p99 evita que unos pocos clientes con razones
extremas dominen el logit.

Auditoria. z, choque, logit y p NUNCA van a Gold: si se guardan para auditoria
es en data/auditoria/, fuera de las sesiones, de la landing y del respaldo. Son
regenerables a partir de la semilla y las features, por eso no se respaldan.

Este modulo no lee ni escribe nada: recibe y devuelve DataFrames. La lectura del
feature store y la escritura de gold_etiquetas_impago estan en generate_labels.py.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

TABLA_ETIQUETAS = "gold_etiquetas_impago"
COLUMNAS_ETIQUETAS = [
    "cliente_id",
    "impago_posterior",
    "fecha_observacion",
    "horizonte_meses",
]

VARIABLES_OBSERVADAS = ["ratio_endeudamiento", "uso_linea_credito", "capacidad_ahorro"]
# Ausencia de producto = 0 (no es dato faltante), igual que en risk_features.py.
SIN_PRODUCTO_ES_CERO = ["ratio_endeudamiento", "uso_linea_credito"]

# Columnas del proceso latente: no pueden aparecer en ninguna tabla de Gold ni en
# la matriz con la que se entrena el modelo.
COLUMNAS_LATENTES = ["z", "choque", "logit", "p"]


@dataclass(frozen=True)
class ConfigEtiquetas:
    semilla: int
    horizonte_meses: int
    tasa_objetivo: float
    b_ratio_endeudamiento: float
    b_uso_linea_credito: float
    b_capacidad_ahorro: float
    b_rasgo_oculto: float
    b_choque: float
    q_choque_mensual: float
    winsor_inferior: float
    winsor_superior: float

    @property
    def prob_choque_horizonte(self) -> float:
        return 1.0 - (1.0 - self.q_choque_mensual) ** self.horizonte_meses


def cargar_config(ruta: str | Path = "config/etiquetas_impago.yaml") -> ConfigEtiquetas:
    """Lee y valida la configuracion. Falla con un mensaje claro si falta una
    llave o un valor esta fuera de rango, en vez de generar etiquetas absurdas."""
    with open(ruta, encoding="utf-8") as f:
        crudo = yaml.safe_load(f) or {}

    try:
        coef = crudo["coeficientes"]
        cfg = ConfigEtiquetas(
            semilla=int(crudo["semilla"]),
            horizonte_meses=int(crudo["horizonte_meses"]),
            tasa_objetivo=float(crudo["tasa_objetivo"]),
            b_ratio_endeudamiento=float(coef["ratio_endeudamiento"]),
            b_uso_linea_credito=float(coef["uso_linea_credito"]),
            b_capacidad_ahorro=float(coef["capacidad_ahorro"]),
            b_rasgo_oculto=float(coef["rasgo_oculto"]),
            b_choque=float(coef["choque"]),
            q_choque_mensual=float(crudo["q_choque_mensual"]),
            winsor_inferior=float(crudo["winsor_inferior"]),
            winsor_superior=float(crudo["winsor_superior"]),
        )
    except KeyError as faltante:
        raise ValueError(f"{ruta}: falta la llave {faltante}") from None

    if cfg.horizonte_meses < 1:
        raise ValueError(f"{ruta}: horizonte_meses debe ser >= 1")
    if not 0.0 < cfg.tasa_objetivo < 1.0:
        raise ValueError(f"{ruta}: tasa_objetivo debe estar en (0, 1)")
    if not 0.0 <= cfg.q_choque_mensual < 1.0:
        raise ValueError(f"{ruta}: q_choque_mensual debe estar en [0, 1)")
    if not 0.0 <= cfg.winsor_inferior < cfg.winsor_superior <= 1.0:
        raise ValueError(
            f"{ruta}: se requiere 0 <= winsor_inferior < winsor_superior <= 1"
        )
    return cfg


def _sigmoide(x: np.ndarray) -> np.ndarray:
    """Sigmoide numericamente estable (sin overflow con logits extremos)."""
    x = np.asarray(x, dtype=float)
    salida = np.empty_like(x)
    pos = x >= 0
    salida[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    e = np.exp(x[~pos])
    salida[~pos] = e / (1.0 + e)
    return salida


def estandarizar_variables(
    features: pd.DataFrame, winsor_inferior: float, winsor_superior: float
) -> pd.DataFrame:
    """Winsoriza cada variable observada en los percentiles dados y la
    estandariza (media 0, desviacion 1) sobre todos los clientes."""
    faltan = [c for c in VARIABLES_OBSERVADAS if c not in features.columns]
    if faltan:
        raise ValueError(f"el feature store no tiene las columnas {faltan}")

    datos = features[VARIABLES_OBSERVADAS].astype(float).copy()
    for col in SIN_PRODUCTO_ES_CERO:
        datos[col] = datos[col].fillna(0.0)
    if datos.isna().any().any():
        malas = datos.columns[datos.isna().any()].tolist()
        raise ValueError(f"valores faltantes inesperados en {malas}")

    for col in datos.columns:
        bajo, alto = np.percentile(
            datos[col], [100 * winsor_inferior, 100 * winsor_superior]
        )
        datos[col] = datos[col].clip(bajo, alto)
        desviacion = datos[col].std(ddof=0)
        # Una columna constante no aporta riesgo: queda en 0 en vez de dividir entre 0.
        datos[col] = (
            0.0 if desviacion == 0 else (datos[col] - datos[col].mean()) / desviacion
        )
    return datos


def calibrar_b0(
    logit_sin_b0: np.ndarray, tasa_objetivo: float, tolerancia: float = 1e-12
) -> float:
    """Encuentra b0 tal que mean(sigmoide(b0 + logit_sin_b0)) == tasa_objetivo,
    por biseccion. La probabilidad media es creciente en b0, asi que el intervalo
    se amplia hasta atrapar la raiz y luego se parte a la mitad."""
    eta = np.asarray(logit_sin_b0, dtype=float)

    def tasa_media(b0: float) -> float:
        return float(_sigmoide(b0 + eta).mean())

    bajo, alto = -10.0, 10.0
    for _ in range(60):
        if tasa_media(bajo) <= tasa_objetivo <= tasa_media(alto):
            break
        bajo, alto = bajo * 2.0, alto * 2.0
    else:
        raise ValueError("no se pudo acotar b0 para la tasa objetivo")

    for _ in range(300):
        medio = 0.5 * (bajo + alto)
        if tasa_media(medio) < tasa_objetivo:
            bajo = medio
        else:
            alto = medio
        if alto - bajo < tolerancia:
            break
    return 0.5 * (bajo + alto)


@dataclass(frozen=True)
class ResultadoSimulacion:
    """Salida del modelo latente. Indexada por cliente_id, en orden de cliente_id."""

    proceso: pd.DataFrame  # z, choque, logit, p, impago (SOLO para auditoria)
    b0: float
    tasa_realizada: float


def simular_evento(
    estandarizadas: pd.DataFrame, cfg: ConfigEtiquetas
) -> ResultadoSimulacion:
    """Genera z, choque y la etiqueta con el modelo latente.

    El resultado depende de la semilla y del conjunto de clientes, no del orden
    de las filas: se ordena por cliente_id antes de sortear. Cada fuente de azar
    usa su propio generador hijo de la misma semilla, para que cambiar una no
    desplace a las otras."""
    if not estandarizadas.index.is_unique:
        raise ValueError("cliente_id repetido en las features")
    datos = estandarizadas.sort_index()
    n = len(datos)

    sec_z, sec_choque, sec_etiqueta = np.random.SeedSequence(cfg.semilla).spawn(3)
    z = np.random.default_rng(sec_z).standard_normal(n)
    choque = (
        np.random.default_rng(sec_choque).random(n) < cfg.prob_choque_horizonte
    ).astype(int)

    logit_sin_b0 = (
        cfg.b_ratio_endeudamiento * datos["ratio_endeudamiento"].to_numpy()
        + cfg.b_uso_linea_credito * datos["uso_linea_credito"].to_numpy()
        - cfg.b_capacidad_ahorro * datos["capacidad_ahorro"].to_numpy()
        + cfg.b_rasgo_oculto * z
        + cfg.b_choque * choque
    )
    b0 = calibrar_b0(logit_sin_b0, cfg.tasa_objetivo)
    logit = b0 + logit_sin_b0
    p = _sigmoide(logit)
    impago = (np.random.default_rng(sec_etiqueta).random(n) < p).astype(int)

    proceso = pd.DataFrame(
        {"z": z, "choque": choque, "logit": logit, "p": p, "impago": impago},
        index=datos.index,
    )
    return ResultadoSimulacion(
        proceso=proceso, b0=b0, tasa_realizada=float(impago.mean())
    )


def construir_tabla_etiquetas(
    resultado: ResultadoSimulacion, fecha_observacion: dt.date, horizonte_meses: int
) -> pd.DataFrame:
    """La tabla que va a Gold (gold_etiquetas_impago): la etiqueta y su fecha de
    observacion, NADA del proceso latente."""
    tabla = pd.DataFrame(
        {
            "cliente_id": resultado.proceso.index.to_numpy(),
            "impago_posterior": resultado.proceso["impago"].to_numpy(),
            "fecha_observacion": fecha_observacion,
            "horizonte_meses": horizonte_meses,
        }
    )
    verificar_sin_latentes(tabla.columns, TABLA_ETIQUETAS)
    return tabla[COLUMNAS_ETIQUETAS]


def construir_auditoria(resultado: ResultadoSimulacion) -> pd.DataFrame:
    """z, choque, logit y p por cliente, para auditar la simulacion. Va a
    data/auditoria/, nunca a Gold."""
    return resultado.proceso[COLUMNAS_LATENTES].reset_index()


def verificar_sin_latentes(columnas, donde: str) -> None:
    """Falla si alguna columna del proceso latente aparece en `donde` (una tabla
    de Gold o la matriz de entrenamiento). Es la barrera contra la fuga: si el
    modelo viera z, choque o p, el AUC dejaria de medir nada."""
    presentes = sorted(set(map(str, columnas)) & set(COLUMNAS_LATENTES))
    if presentes:
        raise ValueError(f"{donde} contiene columnas del proceso latente: {presentes}")


def generar_etiquetas(
    features: pd.DataFrame, cfg: ConfigEtiquetas, fecha_observacion: dt.date
) -> tuple[pd.DataFrame, pd.DataFrame, ResultadoSimulacion]:
    """Punto de entrada: features (indexadas por cliente_id) -> tabla de Gold,
    tabla de auditoria y el resultado completo de la simulacion."""
    verificar_sin_latentes(features.columns, "el feature store")
    estandarizadas = estandarizar_variables(
        features, cfg.winsor_inferior, cfg.winsor_superior
    )
    resultado = simular_evento(estandarizadas, cfg)
    tabla = construir_tabla_etiquetas(resultado, fecha_observacion, cfg.horizonte_meses)
    return tabla, construir_auditoria(resultado), resultado

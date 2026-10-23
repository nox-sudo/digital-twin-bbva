"""
src/gold/explicacion.py

Por que el modelo le da a un cliente su probabilidad de impago, con los
valores SHAP de ese cliente: cuanto empuja cada variable su score hacia
arriba o hacia abajo respecto al cliente promedio del entrenamiento.

Se calculan al vuelo con XGBoost (pred_contribs=True, TreeSHAP exacto), sin
la libreria shap y sin escribir en Gold: para un cliente toma milisegundos.
La suma de las contribuciones mas el valor base es el margen del modelo
(log-odds), asi que la explicacion reproduce exactamente su probabilidad,
siempre que se use el modelo que la produjo: predict_risk.py publica para
los clientes con etiqueta la probabilidad fuera de pliegue, que sale de uno
de los 5 modelos de la validacion cruzada (train_model.py los guarda), no
del modelo final. modelo_del_score() replica esa regla.

Las columnas one-hot de categoria_gasto_dominante se suman en un solo
factor: para el cliente es una sola cosa ("en lo que mas gastas"). La
redaccion de cada variable vive en config/explicacion_riesgo.yaml.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd
import xgboost as xgb
import yaml

from src.common.formato import formato_mxn
from src.gold.risk_features import (
    KPI_CATEGORICO,
    PREFIJO_ONEHOT,
    cargar_features,
    preparar_para_modelo,
)

RUTA_CONFIG = Path(__file__).resolve().parents[2] / "config" / "explicacion_riesgo.yaml"


class SinExplicacion(Exception):
    """No hay como explicar el score publicado con el modelo que lo produjo."""


@dataclass(frozen=True)
class Factor:
    variable: str
    contribucion: float  # log-odds; positivo sube el riesgo
    valor: object
    frase: str

    @property
    def sube(self) -> bool:
        return self.contribucion > 0


@dataclass(frozen=True)
class Explicacion:
    probabilidad: float
    probabilidad_base: float
    factores: list[Factor]  # de mayor a menor efecto, en valor absoluto

    def principales(self, n: int) -> list[Factor]:
        return self.factores[:n]


def cargar_config(ruta: Path = RUTA_CONFIG) -> dict:
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _sigmoide(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def contribuciones(modelo, matriz: pd.DataFrame) -> pd.DataFrame:
    """Contribucion SHAP de cada columna del modelo, por fila, y la columna
    "base" (el valor esperado). Cada fila suma el margen del modelo."""
    datos = xgb.DMatrix(matriz.astype(float), feature_names=list(matriz.columns))
    valores = modelo.get_booster().predict(datos, pred_contribs=True)
    return pd.DataFrame(valores, index=matriz.index, columns=[*matriz.columns, "base"])


def _agrupar_categoria(fila: pd.Series) -> pd.Series:
    onehot = [c for c in fila.index if c.startswith(f"{PREFIJO_ONEHOT}_")]
    agrupada = fila.drop(onehot)
    agrupada[KPI_CATEGORICO] = fila[onehot].sum()
    return agrupada


def _formatear(valor, formato: str, cfg: dict) -> str:
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "sin dato"
    if formato == "pesos":
        return formato_mxn(valor, estilo="cliente")
    if formato == "porcentaje":
        return f"{float(valor) * 100:.0f} %"
    if formato == "veces":
        return f"{float(valor):.1f} veces"
    if formato == "anios":
        return f"{float(valor):.0f} años"
    if formato == "antiguedad":
        anios = float(valor) / 365.25
        return f"{anios:.1f} años" if anios >= 1 else f"{float(valor) / 30.4:.0f} meses"
    if formato == "conteo":
        return f"{float(valor):.0f}"
    if formato == "categoria":
        return cfg["categorias"].get(valor, str(valor).replace("_", " "))
    raise ValueError(f"Formato desconocido: {formato!r}")


def frase(variable: str, valor, cfg: dict) -> str:
    """El hecho de una variable, redactado para el cliente."""
    spec = cfg["variables"][variable]
    if spec["formato"] == "si_no":
        return spec["frase_si"] if valor and not pd.isna(valor) else spec["frase_no"]
    return spec["frase"].format(valor=_formatear(valor, spec["formato"], cfg))


def explicar(modelo, features: pd.DataFrame, cliente_id: str, cfg=None) -> Explicacion:
    """Explicacion del score de un cliente. features es gold_features_cliente
    indexada por cliente_id (risk_features.cargar_features)."""
    cfg = cfg or cargar_config()
    if cliente_id not in features.index:
        raise ValueError(f"{cliente_id} no esta en gold_features_cliente")
    fila_features = features.loc[[cliente_id]]
    matriz = preparar_para_modelo(fila_features, list(modelo.feature_names_in_))
    aportes = contribuciones(modelo, matriz).iloc[0]
    base = float(aportes.pop("base"))
    aportes = _agrupar_categoria(aportes)

    sin_redaccion = set(aportes.index) - set(cfg["variables"])
    if sin_redaccion:
        raise ValueError(
            f"Variables del modelo sin frase en {RUTA_CONFIG.name}: "
            f"{sorted(sin_redaccion)}"
        )
    valores = fila_features.iloc[0]
    factores = [
        Factor(
            variable=v,
            contribucion=float(c),
            valor=valores.get(v),
            frase=frase(v, valores.get(v), cfg),
        )
        for v, c in aportes.items()
    ]
    factores.sort(key=lambda f: abs(f.contribucion), reverse=True)
    return Explicacion(
        probabilidad=_sigmoide(base + float(aportes.sum())),
        probabilidad_base=_sigmoide(base),
        factores=factores,
    )


def modelo_del_score(cliente_id: str, modelo_final, oof, pliegues):
    """El modelo que produjo la probabilidad publicada en Gold, con la misma
    regla que predict_risk.predecir: el del pliegue del cliente si tiene
    probabilidad fuera de pliegue, si no el modelo final."""
    if oof is None:
        return modelo_final
    oof = oof.set_index("cliente_id")
    if cliente_id not in oof.index:
        return modelo_final
    if pliegues is None or "pliegue" not in oof.columns:
        raise SinExplicacion(
            "Faltan los modelos de la validación cruzada; se generan la próxima "
            "vez que se entrene el modelo."
        )
    return pliegues[int(oof.loc[cliente_id, "pliegue"])]


def explicar_cliente(
    ruta_gold: str, ruta_modelo: str, ruta_oof: str, ruta_pliegues: str, cliente_id: str
) -> Explicacion:
    """Lee Gold y los artefactos del modelo y explica el score del cliente."""
    if not Path(ruta_modelo).exists():
        raise SinExplicacion("Todavía no hay un modelo entrenado.")
    modelo_final = joblib.load(ruta_modelo)
    oof = pd.read_parquet(ruta_oof) if Path(ruta_oof).exists() else None
    pliegues = joblib.load(ruta_pliegues) if Path(ruta_pliegues).exists() else None
    modelo = modelo_del_score(cliente_id, modelo_final, oof, pliegues)
    return explicar(modelo, cargar_features(ruta_gold), cliente_id)

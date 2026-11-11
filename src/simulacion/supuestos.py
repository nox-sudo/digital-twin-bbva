"""
src/simulacion/supuestos.py

Carga los supuestos del simulador de hipoteca. config/simulacion.yaml trae los
parametros del escenario (plazo, enganche, gastos, trayectorias, choque de
ingreso); la tasa anual se completa desde config/mercado_mx.yaml, donde cada
cifra de mercado tiene fuente y fecha. La usan el CLI, el dashboard y el
asistente, para que los tres partan del mismo valor.
"""

from pathlib import Path

import yaml

from src.common.mercado import cargar_mercado, tasa_hipotecaria_referencia

RUTA = Path(__file__).resolve().parents[2] / "config" / "simulacion.yaml"


def cargar_simulacion(ruta: Path | str = RUTA, mercado: dict | None = None) -> dict:
    with open(ruta, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    hipoteca = cfg["hipoteca"]
    # Una tasa escrita en simulacion.yaml volveria a tener dos cifras que se
    # pueden contradecir; se rechaza en vez de elegir una en silencio.
    if "tasa_anual" in hipoteca:
        raise ValueError(
            f"{ruta}: hipoteca.tasa_anual no se fija aqui; se toma de "
            "config/mercado_mx.yaml (hipoteca_bbva_fija.tasa_calculo_cat)"
        )
    hipoteca["tasa_anual"] = tasa_hipotecaria_referencia(mercado or cargar_mercado())
    return cfg

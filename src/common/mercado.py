"""
src/common/mercado.py

Cifras de mercado de config/mercado_mx.yaml (tasas de referencia, CAT,
IVA sobre intereses, precio minimo de vivienda). Las leen el simulador, el
asistente y las compras a credito; cada cifra trae su fuente y fechas.
"""

from pathlib import Path

import yaml

RUTA = Path(__file__).resolve().parents[2] / "config" / "mercado_mx.yaml"


def cargar_mercado(ruta: Path = RUTA) -> dict:
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def iva_sobre_intereses(producto: str, mercado: dict | None = None) -> float:
    """Tasa de IVA que pagan los intereses de un credito: 0 para hipoteca de
    vivienda (LIVA art. 15, fr. X, inc. d), la tasa general para tarjeta,
    prestamo personal y credito de consumo de persona fisica."""
    mercado = mercado or cargar_mercado()
    iva = mercado["iva"]
    if producto not in iva["intereses"] or not isinstance(
        iva["intereses"][producto], bool
    ):
        raise ValueError(f"Producto sin regla de IVA en mercado_mx.yaml: {producto!r}")
    return float(iva["tasa_general"]["valor"]) if iva["intereses"][producto] else 0.0


def precio_minimo_vivienda(mercado: dict | None = None) -> float:
    """Precio minimo de una vivienda social (SHF): por debajo, la capacidad
    hipotecaria no alcanza para ninguna vivienda."""
    mercado = mercado or cargar_mercado()
    return float(mercado["vivienda_social_precio_minimo"]["valor"])

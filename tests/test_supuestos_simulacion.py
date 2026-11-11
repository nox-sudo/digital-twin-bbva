"""La tasa del simulador de hipoteca sale de config/mercado_mx.yaml, con
fuente, y no de un valor fijo en config/simulacion.yaml."""

import sys
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from src.common.mercado import cargar_mercado  # noqa: E402
from src.simulacion.supuestos import cargar_simulacion  # noqa: E402


def test_tasa_viene_de_mercado_con_fuente():
    mercado = cargar_mercado()
    referencia = mercado["hipoteca_bbva_fija"]
    cfg = cargar_simulacion()
    assert cfg["hipoteca"]["tasa_anual"] == referencia["tasa_calculo_cat"]
    assert referencia["tipo_fuente"] == "primaria"
    assert referencia["url"].startswith("https://")


def test_simulacion_yaml_no_fija_tasa():
    with open(RAIZ / "config" / "simulacion.yaml", encoding="utf-8") as f:
        crudo = yaml.safe_load(f)
    assert "tasa_anual" not in crudo["hipoteca"]


def test_sigue_la_cifra_de_mercado():
    """Si cambia la cifra en mercado_mx.yaml, cambia el simulador."""
    mercado = cargar_mercado()
    mercado["hipoteca_bbva_fija"]["tasa_calculo_cat"] = 0.0999
    assert cargar_simulacion(mercado=mercado)["hipoteca"]["tasa_anual"] == 0.0999


def test_rechaza_una_segunda_tasa(tmp_path):
    with open(RAIZ / "config" / "simulacion.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["hipoteca"]["tasa_anual"] = 0.11
    ruta = tmp_path / "simulacion.yaml"
    ruta.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ValueError, match="mercado_mx.yaml"):
        cargar_simulacion(ruta)


def test_conserva_los_supuestos_del_escenario():
    cfg = cargar_simulacion()
    for clave in ("plazo_anios", "enganche_pct", "gastos_iniciales_pct"):
        assert clave in cfg["hipoteca"]
    assert {"simulacion", "choque_ingreso", "incremento_deficit_maximo"} <= set(cfg)

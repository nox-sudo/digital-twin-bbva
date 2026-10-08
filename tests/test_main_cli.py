"""
tests/test_main_cli.py

Pruebas del CLI unico (main.py). No corren ningun paso real del
pipeline (eso ya lo cubren los otros tests y el smoke test de CI) -
solo verifican el enrutamiento: que un paso invalido falle con
claridad, y que cada nombre de paso mapee a un modulo importable.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


def test_sin_argumentos_sale_con_error():
    resultado = subprocess.run(
        [sys.executable, "main.py"], capture_output=True, text=True
    )
    assert resultado.returncode == 1
    assert "Pasos disponibles" in resultado.stdout


def test_paso_invalido_sale_con_error():
    resultado = subprocess.run(
        [sys.executable, "main.py", "no-existe"], capture_output=True, text=True
    )
    assert resultado.returncode == 1
    assert "Pasos disponibles" in resultado.stdout


def test_help_sale_sin_error():
    resultado = subprocess.run(
        [sys.executable, "main.py", "--help"], capture_output=True, text=True
    )
    assert resultado.returncode == 0


@pytest.mark.parametrize("nombre_modulo", main.PASOS.values())
def test_cada_paso_mapea_a_un_modulo_importable(nombre_modulo):
    """No importa el modulo (algunos pasos traen dependencias pesadas
    como Spark o XGBoost) - solo confirma que el archivo existe y es
    localizable, para detectar un typo en PASOS antes de que falle en
    produccion."""
    import importlib.util

    assert importlib.util.find_spec(nombre_modulo) is not None

"""
tests/test_generador_perfiles.py

Perfil financiero estable de cada cliente en el generador sintetico
(generate_synthetic_sources.generar_perfiles y su uso por mes).
"""

import random
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_synthetic_sources as generador  # noqa: E402


@pytest.fixture
def poblacion():
    generador.sembrar(7)
    clientes = generador.generar_clientes(60, date(2026, 9, 30))
    cuentas = generador.generar_cuentas(clientes, date(2026, 9, 30))
    return clientes, cuentas


def _mes(cuentas, perfiles, anio, mes):
    semilla = generador.semilla_mes(7, anio, mes)
    random.seed(semilla)
    np.random.seed(semilla)
    generador.Faker.seed(semilla)  # nombres de contraparte P2P
    return generador.generar_transacciones_mes(cuentas, anio, mes, perfiles)


def test_perfiles_deterministas_y_sin_tocar_el_estado_global(poblacion):
    clientes, cuentas = poblacion
    estado_antes = np.random.get_state()[1].copy()
    a = generador.generar_perfiles(clientes, cuentas, 7)
    b = generador.generar_perfiles(clientes, cuentas, 7)
    assert (np.random.get_state()[1] == estado_antes).all()
    assert a.keys() == b.keys()
    for cliente_id in a:
        assert a[cliente_id]["ingreso_base"] == b[cliente_id]["ingreso_base"]


def test_nomina_formal_fija_y_anclada_al_ingreso_declarado(poblacion):
    clientes, cuentas = poblacion
    perfiles = generador.generar_perfiles(clientes, cuentas, 7)
    formal = next(c for c, p in perfiles.items() if p["formal"])
    declarado = clientes.set_index("cliente_id").loc[
        formal, "ingreso_mensual_declarado"
    ]

    ingresos = []
    for mes in (7, 8, 9):
        tx = _mes(cuentas, perfiles, 2026, mes)
        nomina = tx[(tx["cliente_id"] == formal) & (tx["tipo_transaccion"] == "nomina")]
        assert len(nomina) == 2  # dos quincenas, todos los meses
        ingresos.append(nomina["monto"].sum())

    assert ingresos[0] == ingresos[1] == ingresos[2]
    assert 0.8 * declarado <= ingresos[0] <= 1.06 * declarado


def test_informales_reciben_depositos_no_nomina(poblacion):
    clientes, cuentas = poblacion
    perfiles = generador.generar_perfiles(clientes, cuentas, 7)
    informales = {c for c, p in perfiles.items() if not p["formal"]}
    tx = _mes(cuentas, perfiles, 2026, 8)
    de_informales = tx[tx["cliente_id"].isin(informales)]
    assert (de_informales["tipo_transaccion"] == "nomina").sum() == 0
    assert (de_informales["tipo_transaccion"] == "deposito").sum() > 0


def test_un_mes_no_depende_de_los_demas(poblacion):
    """La semilla por mes sigue garantizando que agregar una entrega no
    cambia los meses anteriores."""
    clientes, cuentas = poblacion
    perfiles = generador.generar_perfiles(clientes, cuentas, 7)
    agosto_1 = _mes(cuentas, perfiles, 2026, 8)
    _mes(cuentas, perfiles, 2026, 9)
    agosto_2 = _mes(cuentas, perfiles, 2026, 8)
    pd.testing.assert_frame_equal(agosto_1, agosto_2)

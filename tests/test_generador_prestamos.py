"""
tests/test_generador_prestamos.py

Prestamos personales en el generador sintetico: el saldo sale de la tabla de
amortizacion y la cuota solo se paga dentro del plazo.
"""

import random
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_synthetic_sources as generador  # noqa: E402

FECHA_BASE = date(2026, 9, 30)
# Doce meses base mas siete entregas, como la instancia del 2026-10-23.
MESES = sorted(generador.meses_a_generar(date(2027, 4, 30), 19))


@pytest.fixture(scope="module")
def poblacion():
    generador.sembrar(7)
    clientes = generador.generar_clientes(200, FECHA_BASE)
    cuentas = generador.generar_cuentas(clientes, FECHA_BASE)
    perfiles = generador.generar_perfiles(clientes, cuentas, 7)
    return cuentas, perfiles


def _prestamos(cuentas):
    return [c for c in cuentas if c["tipo_cuenta"] == "prestamo_personal"]


def _mes(cuentas, perfiles, anio, mes):
    semilla = generador.semilla_mes(7, anio, mes)
    random.seed(semilla)
    np.random.seed(semilla)
    generador.Faker.seed(semilla)
    return generador.generar_transacciones_mes(cuentas, anio, mes, perfiles)


def test_saldo_sigue_la_tabla_de_amortizacion():
    monto, plazo = 81_000.0, 12
    cuota = generador._cuota_prestamo(monto, plazo)
    tasa = generador.TASA_PRESTAMO_PERSONAL / 12
    saldo = monto
    for k in range(plazo + 1):
        assert generador._saldo_prestamo(monto, plazo, k) == pytest.approx(
            saldo, abs=0.01
        )
        saldo = saldo * (1 + tasa) - cuota
    assert generador._saldo_prestamo(monto, plazo, plazo) == 0
    assert generador._saldo_prestamo(monto, plazo, plazo + 5) == 0
    assert generador._saldo_prestamo(monto, plazo, -1) == monto


def test_saldo_en_cuentas_es_el_de_la_fecha_base(poblacion):
    cuentas, _ = poblacion
    prestamos = _prestamos(cuentas)
    assert prestamos
    for p in prestamos:
        vencidas = generador._meses_desde_apertura(p["fecha_apertura"], 2026, 9)
        esperado = generador._saldo_prestamo(
            p["monto_original"], p["plazo_meses"], vencidas
        )
        assert p["saldo_actual"] == esperado
        if vencidas >= p["plazo_meses"]:
            assert p["saldo_actual"] == 0
        elif vencidas >= 1:
            assert 0 < p["saldo_actual"] < p["monto_original"]
    # La poblacion trae de los dos casos: liquidados y vigentes.
    assert any(p["saldo_actual"] == 0 for p in prestamos)
    assert any(p["saldo_actual"] > 0 for p in prestamos)


def test_la_cuota_solo_se_paga_dentro_del_plazo(poblacion):
    cuentas, perfiles = poblacion
    # A lo mas un prestamo por cliente: el pago se identifica por cliente.
    prestamo_de = {p["cliente_id"]: p for p in _prestamos(cuentas)}
    pagos_en_plazo = 0
    for anio, mes in MESES:
        tx = _mes(cuentas, perfiles, anio, mes)
        pagos = tx[tx["contraparte"] == "Pago Préstamo Personal"]
        for cliente_id in pagos["cliente_id"]:
            p = prestamo_de[cliente_id]
            transcurridos = generador._meses_desde_apertura(
                p["fecha_apertura"], anio, mes
            )
            assert 1 <= transcurridos <= p["plazo_meses"], (cliente_id, anio, mes)
            pagos_en_plazo += 1
    assert pagos_en_plazo > 0


def test_el_plazo_solo_quita_pagos_de_prestamo(poblacion):
    """Revisar el plazo consume los mismos sorteos y avanza el contador igual:
    fuera de las cuotas de prestamo, el mes sale identico al de una poblacion
    con todos los prestamos vigentes (ids incluidos)."""
    cuentas, perfiles = poblacion
    vigentes = [
        (
            dict(c, fecha_apertura="2025-01-15", plazo_meses=48)
            if c["tipo_cuenta"] == "prestamo_personal"
            else c
        )
        for c in cuentas
    ]
    quitados = 0
    for anio, mes in MESES:
        real = _mes(cuentas, perfiles, anio, mes)
        todos = _mes(vigentes, perfiles, anio, mes)
        es_prestamo = todos["contraparte"] == "Pago Préstamo Personal"
        quitados += len(todos) - len(real)
        assert (
            real[real["contraparte"] != "Pago Préstamo Personal"]
            .reset_index(drop=True)
            .equals(todos[~es_prestamo].reset_index(drop=True))
        )
        assert set(real["transaccion_id"]) <= set(todos["transaccion_id"])
    assert quitados > 0

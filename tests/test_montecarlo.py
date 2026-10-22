"""
tests/test_montecarlo.py

Simulador Monte Carlo de hipoteca (src/simulacion/montecarlo.py): la
formula de la cuota, el determinismo, la contabilidad del saldo, el
choque de ingreso y la busqueda de capacidad maxima.
"""

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.simulacion import montecarlo as mc  # noqa: E402


def _historial(ingreso, salidas, otras=0.0, meses=12) -> mc.Historial:
    return mc.Historial(
        meses=tuple(f"2026-{m:02d}" for m in range(1, meses + 1)),
        ingreso_laboral=np.full(meses, float(ingreso)),
        otras_entradas=np.full(meses, float(otras)),
        salidas=np.full(meses, float(salidas)),
    )


def _hipoteca(precio, renta=0.0) -> mc.Hipoteca:
    return mc.Hipoteca(
        precio=precio,
        tasa_anual=0.11,
        plazo_anios=20,
        enganche_pct=0.10,
        gastos_iniciales_pct=0.05,
        renta_reemplazada=renta,
    )


SIN_CHOQUE = mc.Supuestos(
    meses=24, trayectorias=500, semilla=1, prob_choque_mensual=0.0
)


def test_cuota_fija_contra_valor_conocido():
    # 1,000,000 a 20 anios y 11 % anual: 10,321.89 (tabla de amortizacion)
    assert mc.cuota_fija(1_000_000, 0.11, 240) == pytest.approx(10321.89, abs=0.01)
    assert mc.cuota_fija(120_000, 0.0, 12) == pytest.approx(10_000)
    assert mc.cuota_fija(0, 0.11, 240) == 0.0


def test_sin_variacion_el_saldo_es_aritmetica_exacta():
    """Ingreso y salidas constantes, sin choque: el saldo de cada mes es
    saldo inicial + mes x (neto - cuota), en todas las trayectorias."""
    historial = _historial(ingreso=20_000, salidas=15_000)
    muestras = mc.muestrear(len(historial.meses), SIN_CHOQUE)
    saldos = mc.simular(historial, 10_000, muestras, cuota_mensual=3_000)
    esperado = 10_000 + np.arange(25) * (20_000 - 15_000 - 3_000)
    assert np.allclose(saldos, esperado)


def test_misma_semilla_mismo_resultado():
    historial = mc.Historial(
        meses=tuple("abcdef"),
        ingreso_laboral=np.array([10e3, 0, 25e3, 12e3, 8e3, 30e3]),
        otras_entradas=np.zeros(6),
        salidas=np.array([12e3, 9e3, 14e3, 11e3, 13e3, 10e3]),
    )
    supuestos = mc.Supuestos(meses=24, trayectorias=300, semilla=7)
    a = mc.resumir(mc.simular(historial, 5_000, mc.muestrear(6, supuestos)))
    b = mc.resumir(mc.simular(historial, 5_000, mc.muestrear(6, supuestos)))
    assert a.probabilidad_deficit == b.probabilidad_deficit
    pd.testing.assert_frame_equal(a.bandas, b.bandas)


def test_el_choque_quita_el_ingreso_y_genera_deficit():
    """Un cliente que ahorra 2,000 al mes nunca cae sin choque; con choques
    frecuentes de varios meses sin ingreso, si."""
    historial = _historial(ingreso=20_000, salidas=18_000)
    sin = mc.resumir(mc.simular(historial, 5_000, mc.muestrear(12, SIN_CHOQUE)))
    con_choque = mc.Supuestos(
        meses=24,
        trayectorias=500,
        semilla=1,
        prob_choque_mensual=0.2,
        duracion_choque=(3, 3),
    )
    con = mc.resumir(mc.simular(historial, 5_000, mc.muestrear(12, con_choque)))
    assert sin.probabilidad_deficit == 0.0
    assert con.probabilidad_deficit > 0.5


def test_los_choques_duran_al_menos_el_minimo_y_ocurren_a_la_tasa_esperada():
    supuestos = mc.Supuestos(
        meses=60,
        trayectorias=2000,
        semilla=3,
        prob_choque_mensual=0.05,
        duracion_choque=(2, 4),
    )
    en_choque = mc.muestrear(12, supuestos).en_choque
    for fila in en_choque[:200]:
        cambios = np.diff(np.concatenate([[0], fila.astype(int), [0]]))
        inicios, fines = np.where(cambios == 1)[0], np.where(cambios == -1)[0]
        for inicio, fin in zip(inicios, fines):
            if fin < supuestos.meses:  # una racha cortada por el horizonte no cuenta
                assert fin - inicio >= 2
    # Proporcion de meses en choque en estado estable: p*d / (1 + p*d), con d = 3
    esperado = 0.05 * 3 / (1 + 0.05 * 3)
    assert en_choque[:, 12:].mean() == pytest.approx(esperado, rel=0.1)


def test_la_renta_reemplazada_reduce_el_cargo():
    historial = _historial(ingreso=25_000, salidas=20_000)
    muestras = mc.muestrear(12, SIN_CHOQUE)
    sin_renta = mc.evaluar_hipoteca(historial, 10_000, _hipoteca(900_000), muestras)
    con_renta = mc.evaluar_hipoteca(
        historial, 10_000, _hipoteca(900_000, renta=7_000), muestras
    )
    assert sin_renta.probabilidad_deficit == 1.0  # cuota 8,361 > ahorro 5,000
    assert con_renta.probabilidad_deficit == 0.0  # cargo neto 1,361 < 5,000


def test_capacidad_maxima_esta_en_la_frontera():
    """Sin variacion ni choque, la frontera es exacta: la cuota maxima es el
    ahorro mensual (5,000) mas lo que el saldo inicial cubre en 24 meses."""
    historial = _historial(ingreso=25_000, salidas=20_000)
    muestras = mc.muestrear(12, SIN_CHOQUE)
    resultado = mc.capacidad_maxima(historial, 0.0, _hipoteca(1), muestras, 0.0)
    assert resultado.aplica and resultado.motivo is None
    capacidad = resultado.monto

    cuota_en_capacidad = _hipoteca(capacidad).cuota_mensual
    cuota_un_poco_arriba = _hipoteca(capacidad + 20_000).cuota_mensual
    assert cuota_en_capacidad <= 5_000
    assert cuota_un_poco_arriba > 5_000


def test_meses_para_juntar_el_enganche():
    historial = _historial(ingreso=25_000, salidas=20_000)
    assert mc.meses_para_juntar(60_000, historial, 10_000) == pytest.approx(10)
    assert mc.meses_para_juntar(5_000, historial, 10_000) == 0.0
    sin_ahorro = _historial(ingreso=20_000, salidas=21_000)
    assert mc.meses_para_juntar(60_000, sin_ahorro, 0) is None


def test_historial_desde_gold_rellena_meses_sin_movimientos(tmp_path):
    ruta = str(tmp_path / "kpis.duckdb")
    movimientos = pd.DataFrame(
        {
            "cliente_id": ["CLI-1", "CLI-1", "CLI-1", "CLI-2"],
            "mes": ["2026-07", "2026-07", "2026-08", "2026-09"],
            "tipo_transaccion": ["nomina", "compra", "compra", "nomina"],
            "categoria": ["ingreso", "comida", "comida", "ingreso"],
            "monto_total": [20_000.0, -8_000.0, -9_000.0, 15_000.0],
            "n_movimientos": [2, 5, 6, 2],
        }
    )
    con = duckdb.connect(ruta)
    con.register("df", movimientos)
    con.execute("CREATE TABLE gold_movimientos_mensuales AS SELECT * FROM df")
    con.close()

    historial = mc.historial_cliente(ruta, "CLI-1")
    # 3 meses en la tabla; CLI-1 no tiene nada en septiembre
    assert historial.meses == ("2026-07", "2026-08", "2026-09")
    assert historial.ingreso_laboral.tolist() == [20_000, 0, 0]
    assert historial.salidas.tolist() == [8_000, 9_000, 0]
    with pytest.raises(ValueError, match="CLI-NO"):
        mc.historial_cliente(ruta, "CLI-NO")


TOPE = 20_000_000


def test_riesgo_saturado_no_aplica_en_vez_del_tope():
    """Gasta mas de lo que gana: sin hipoteca ya queda en negativo en el 100 %
    de los escenarios. Antes la capacidad salia en el tope (20,000,000)."""
    historial = _historial(ingreso=15_000, salidas=18_000)
    muestras = mc.muestrear(12, SIN_CHOQUE)
    resultado = mc.capacidad_maxima(historial, 0.0, _hipoteca(1), muestras, 0.05)
    assert resultado.monto is None
    assert resultado.motivo == mc.RIESGO_SATURADO
    assert "100 %" in resultado.detalle


def test_riesgo_en_95_por_ciento_tambien_satura():
    """Justo en el borde: 95 % sin hipoteca mas 5 puntos llega a 100 %."""
    historial = _historial(ingreso=15_000, salidas=18_000)
    muestras = mc.muestrear(12, SIN_CHOQUE)
    saldo = 0.0
    riesgo = mc.resumir(mc.simular(historial, saldo, muestras)).probabilidad_deficit
    incremento = 1 - riesgo  # el limite queda exactamente en 1
    resultado = mc.capacidad_maxima(
        historial, saldo, _hipoteca(1), muestras, incremento
    )
    assert resultado.motivo == mc.RIESGO_SATURADO


def test_supera_el_tope_no_aplica():
    historial = _historial(ingreso=10_000_000, salidas=0)
    muestras = mc.muestrear(12, SIN_CHOQUE)
    resultado = mc.capacidad_maxima(historial, 0.0, _hipoteca(1), muestras, 0.0)
    assert resultado.monto is None
    assert resultado.motivo == mc.SUPERA_TOPE


@pytest.mark.parametrize(
    "ingreso, salidas, saldo",
    [
        (15_000, 18_000, 0.0),
        (15_000, 14_900, 0.0),
        (25_000, 20_000, 0.0),
        (25_000, 20_000, 500_000.0),
        (9_582.47, 9_000, 2_000.0),
        (60_000, 30_000, 50_000.0),
        (10_000_000, 0, 0.0),
    ],
)
def test_ningun_cliente_sale_con_el_tope_como_capacidad(ingreso, salidas, saldo):
    historial = _historial(ingreso=ingreso, salidas=salidas)
    supuestos = mc.Supuestos(meses=36, trayectorias=500, semilla=42)
    muestras = mc.muestrear(len(historial.meses), supuestos)
    resultado = mc.capacidad_maxima(historial, saldo, _hipoteca(1), muestras, 0.05)
    if resultado.aplica:
        assert resultado.monto < TOPE - 10_000
    else:
        assert resultado.motivo in (mc.RIESGO_SATURADO, mc.SUPERA_TOPE)
        assert resultado.detalle

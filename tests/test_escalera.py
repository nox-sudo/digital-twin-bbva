"""
tests/test_escalera.py

Escalera financiera (src/gold/escalera.py) sobre un Gold minimo: un cliente
por cada paso, uno sin tarjeta ni prestamo, uno con ingreso variable y uno que
dejo de pagar. Ademas, la configuracion (cada umbral con su fuente) y el
formato de montos.
"""

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.formato import formato_mxn  # noqa: E402
from src.gold import escalera as esc  # noqa: E402

MESES = [f"2026-{m:02d}" for m in range(1, 13)]

# cliente: (ingreso por mes o lista, tipo de ingreso, salidas por mes, saldo,
#           creditos [(tipo, saldo, limite, monto_original, plazo)])
CLIENTES = {
    "DEUDA": (
        10_000,
        "nomina",
        4_000,
        5_000,
        [("prestamo_personal", 100_000, None, 120_000, 24)],
    ),
    "TARJETA": (
        20_000,
        "nomina",
        15_000,
        5_000,
        [("tarjeta_credito", 9_000, 10_000, None, None)],
    ),
    "FONDO_MIN": (20_000, "nomina", 15_000, 10_000, []),
    "AHORRO": (20_000, "nomina", 19_000, 60_000, []),
    "FONDO_META": (20_000, "nomina", 15_000, 60_000, []),
    "INVERTIR": (
        20_000,
        "nomina",
        15_000,
        120_000,
        [("tarjeta_credito", 1_000, 10_000, None, None)],
    ),
    "SIN_CREDITO": (20_000, "nomina", 15_000, 120_000, []),
    "VARIABLE": (
        [6_000, 14_000] * 6,
        "deposito",
        7_000,
        5_000,
        [("prestamo_personal", 40_000, None, 60_000, 36)],
    ),
    "NO_PAGA": (
        10_000,
        "nomina",
        4_000,
        5_000,
        [("prestamo_personal", 100_000, None, 120_000, 24)],
    ),
}


def crear_gold(tmp_path) -> str:
    ruta = str(tmp_path / "kpis.duckdb")
    features, movimientos, creditos = [], [], []
    for cliente, (ingreso, tipo, salidas, saldo, cuentas) in CLIENTES.items():
        features.append(
            {"cliente_id": cliente, "saldo_liquido_disponible": float(saldo)}
        )
        ingresos = ingreso if isinstance(ingreso, list) else [ingreso] * 12
        comprometido = esc.pago_comprometido(
            pd.DataFrame(
                [
                    {
                        "tipo_cuenta": t,
                        "saldo_actual": s,
                        "limite_credito": lim,
                        "monto_original": mo,
                        "plazo_meses": pl,
                    }
                    for t, s, lim, mo, pl in cuentas
                ],
                columns=[
                    "tipo_cuenta",
                    "saldo_actual",
                    "limite_credito",
                    "monto_original",
                    "plazo_meses",
                ],
            ),
            esc.cargar_config(),
        )
        for i, mes in enumerate(MESES):
            movimientos.append((cliente, mes, tipo, "ingreso", float(ingresos[i])))
            movimientos.append((cliente, mes, "compra", "comida", -float(salidas)))
            no_paga = cliente == "NO_PAGA" and i >= 9
            if comprometido and not no_paga:
                movimientos.append(
                    (cliente, mes, "pago_recurrente", "pago_deuda", -comprometido)
                )
        for j, (t, s, lim, mo, pl) in enumerate(cuentas):
            creditos.append(
                {
                    "cliente_id": cliente,
                    "cuenta_id": f"{cliente}-{j}",
                    "tipo_cuenta": t,
                    "saldo_actual": float(s),
                    "limite_credito": lim,
                    "monto_original": mo,
                    "plazo_meses": pl,
                    "fecha_apertura": pd.Timestamp("2025-01-01").date(),
                }
            )
    mov = pd.DataFrame(
        movimientos,
        columns=["cliente_id", "mes", "tipo_transaccion", "categoria", "monto_total"],
    )
    mov["n_movimientos"] = 1
    con = duckdb.connect(ruta)
    for nombre, df in [
        ("gold_features_cliente", pd.DataFrame(features)),
        ("gold_movimientos_mensuales", mov),
        ("gold_creditos_cliente", pd.DataFrame(creditos)),
    ]:
        con.register("df", df)
        con.execute(f"CREATE TABLE {nombre} AS SELECT * FROM df")
        con.unregister("df")
    con.close()
    return ruta


@pytest.fixture
def gold(tmp_path):
    return crear_gold(tmp_path)


@pytest.mark.parametrize(
    "cliente, paso",
    [
        ("DEUDA", "pagos_deuda"),
        ("TARJETA", "uso_tarjeta"),
        ("FONDO_MIN", "fondo_minimo"),
        ("AHORRO", "ahorro"),
        ("FONDO_META", "fondo_meta"),
        ("INVERTIR", "invertir"),
    ],
)
def test_un_cliente_por_paso(gold, cliente, paso):
    e = esc.calcular(gold, cliente, 12)
    assert e.paso_actual == paso
    assert e.numero_paso == esc.cargar_config()["orden"].index(paso) + 1
    assert e.lectura


def test_sin_tarjeta_ni_prestamo(gold):
    e = esc.calcular(gold, "SIN_CREDITO", 12)
    assert e.paso_actual == "invertir"
    assert e.metricas["pago_comprometido"] == 0
    assert e.metricas["razon_pagos_deuda"] == 0
    tarjeta = next(p for p in e.pasos if p.paso == "uso_tarjeta")
    assert tarjeta.cumple and not tarjeta.aplica and tarjeta.valor is None
    assert e.cumplimiento["meses_sin_pago"] == 0
    assert e.supuestos == []


def test_la_deuda_se_mide_con_el_pago_no_con_el_saldo(gold):
    e = esc.calcular(gold, "DEUDA", 12)
    cuota = esc.cuota_prestamo(120_000, 24, 0.28, 0.16)
    assert e.metricas["pago_comprometido"] == pytest.approx(cuota)
    assert e.metricas["razon_pagos_deuda"] == pytest.approx(cuota / 10_000)
    # Con el saldo (100,000 / 10,000 = 10) el numero seria otro: no se usa.
    assert e.metricas["razon_pagos_deuda"] < 1


def test_ingreso_variable_usa_el_mes_de_menor_ingreso(gold):
    e = esc.calcular(gold, "VARIABLE", 12)
    assert e.metricas["ingreso_variable"] is True
    assert e.metricas["ingreso_referencia"] == 6_000
    assert e.metricas["ingreso_mensual_promedio"] == 10_000
    cuota = esc.cuota_prestamo(60_000, 36, 0.28, 0.16)
    # Con el promedio pasaria (cuota / 10,000 < 0.30); con el mes bajo no.
    assert cuota / 10_000 <= 0.30 < cuota / 6_000
    assert e.paso_actual == "pagos_deuda"
    assert "mes de menor ingreso" in e.lectura
    assert any("CONDUSEF" in s for s in e.supuestos)


def test_el_pago_observado_es_senal_no_medida(gold):
    deuda, no_paga = esc.calcular(gold, "DEUDA", 12), esc.calcular(gold, "NO_PAGA", 12)
    assert no_paga.metricas["razon_pagos_deuda"] == deuda.metricas["razon_pagos_deuda"]
    assert no_paga.paso_actual == deuda.paso_actual
    assert deuda.cumplimiento["meses_sin_pago"] == 0
    assert no_paga.cumplimiento["meses_sin_pago"] == 3
    assert "no registramos pagos de tus deudas" in no_paga.lectura


def test_pago_minimo_de_tarjeta_segun_banxico():
    cfg = esc.cargar_config()["pago_comprometido"]["pago_minimo_tarjeta"]
    # Saldo alto: domina 1.5 % del saldo mas intereses con IVA.
    alto = esc.pago_minimo_tarjeta(10_000, 30_000, 0.45, 0.16, cfg)
    assert alto == pytest.approx(150 + 10_000 * 0.45 / 12 * 1.16)
    # Saldo chico con limite grande: domina 1.25 % del limite, topado al saldo.
    assert esc.pago_minimo_tarjeta(300, 100_000, 0.45, 0.16, cfg) == 300
    assert esc.pago_minimo_tarjeta(2_000, 100_000, 0.45, 0.16, cfg) == pytest.approx(
        1_250
    )
    assert esc.pago_minimo_tarjeta(0, 100_000, 0.45, 0.16, cfg) == 0


def test_cada_umbral_trae_su_fuente():
    cfg = esc.cargar_config()
    externas = list(cfg["umbrales"].values()) + [
        cfg["pago_comprometido"][k]
        for k in ("pago_minimo_tarjeta", "iva_intereses", "tasas_anuales")
    ]
    for cifra in externas:
        assert {"fuente", "url", "tipo_fuente", "fecha_consulta"} <= set(cifra)
        assert cifra["tipo_fuente"] in ("primaria", "secundaria", "propia")
        if cifra["tipo_fuente"] != "propia":
            assert cifra["url"].startswith("https://") and cifra["fecha_dato"]
    assert cfg["umbrales"]["pagos_deuda_max"]["valor"] == 0.30
    assert cfg["umbrales"]["uso_tarjeta_max"]["tipo_fuente"] == "propia"


def test_la_lectura_usa_pesos_sin_dolares(gold):
    for cliente in CLIENTES:
        lectura = esc.calcular(gold, cliente, 12).lectura
        assert "USD" not in lectura and "dólar" not in lectura.lower()
        assert " MXN" not in lectura  # estilo cliente


@pytest.mark.parametrize(
    "valor, estilo, esperado",
    [
        (20407, "admin", "$20,407 MXN"),
        (20407, "cliente", "$20,407"),
        (-4091.4, "admin", "-$4,091 MXN"),
        (-0.4, "cliente", "$0"),
        (None, "admin", "sin dato"),
        (float("nan"), "cliente", "sin dato"),
    ],
)
def test_formato_mxn(valor, estilo, esperado):
    assert formato_mxn(valor, estilo=estilo) == esperado


def test_formato_mxn_rechaza_estilos_desconocidos():
    with pytest.raises(ValueError):
        formato_mxn(1, estilo="usd")

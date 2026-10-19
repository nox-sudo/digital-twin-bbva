"""
tests/test_kpis.py

Definiciones de los KPIs de ingreso, gasto y ahorro
(src/gold/kpi_definitions.py) sobre un Silver minimo en memoria.
"""

import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gold import kpi_definitions as kpis  # noqa: E402


@pytest.fixture
def ctx(spark):
    """CLI-1 cobra nomina en 2 de 3 meses; CLI-2 tiene ingreso informal y
    un prestamo."""
    d = Decimal
    filas = [
        ("CLI-1", dt.date(2026, 7, 15), "nomina", d("10000")),
        ("CLI-1", dt.date(2026, 7, 31), "nomina", d("10000")),
        ("CLI-1", dt.date(2026, 7, 3), "compra", d("-6000")),
        ("CLI-1", dt.date(2026, 8, 3), "compra", d("-6000")),  # agosto sin nomina
        ("CLI-1", dt.date(2026, 9, 15), "nomina", d("10000")),
        ("CLI-1", dt.date(2026, 9, 30), "nomina", d("10000")),
        ("CLI-1", dt.date(2026, 9, 3), "compra", d("-6000")),
        ("CLI-2", dt.date(2026, 7, 9), "deposito", d("9000")),
        ("CLI-2", dt.date(2026, 8, 9), "deposito", d("12000")),
        ("CLI-2", dt.date(2026, 9, 9), "deposito", d("6000")),
        ("CLI-2", dt.date(2026, 7, 10), "pago_recurrente", d("-3000")),
        ("CLI-2", dt.date(2026, 8, 10), "pago_recurrente", d("-3000")),
        ("CLI-2", dt.date(2026, 9, 10), "pago_recurrente", d("-3000")),
        ("CLI-2", dt.date(2026, 8, 20), "p2p_recibido", d("900")),
    ]
    tx = spark.createDataFrame(
        filas,
        "cliente_id string, fecha date, tipo_transaccion string, monto decimal(12,2)",
    )
    clientes = spark.createDataFrame([("CLI-1",), ("CLI-2",)], "cliente_id string")
    return {"transacciones": tx, "clientes": clientes}


def _valor(df, cliente_id):
    return df.set_index("cliente_id").loc[cliente_id, "valor_numerico"]


def test_un_mes_sin_ingreso_cuenta_como_cero(ctx):
    """CLI-1 cobra 20,000 en 2 de 3 meses: su promedio es 13,333, no 20,000."""
    ingreso = kpis.kpi_ingreso_mensual_promedio(ctx)
    assert _valor(ingreso, "CLI-1") == pytest.approx(40000 / 3)


def test_el_deposito_informal_es_ingreso_y_el_p2p_no(ctx):
    ingreso = kpis.kpi_ingreso_mensual_promedio(ctx)
    assert _valor(ingreso, "CLI-2") == pytest.approx(9000)


def test_la_irregularidad_se_ve_en_la_estabilidad(ctx):
    estabilidad = kpis.kpi_estabilidad_ingreso(ctx)
    # serie [20000, 0, 20000] -> desviacion muestral 11,547
    assert _valor(estabilidad, "CLI-1") == pytest.approx(11547.0, rel=1e-3)


def test_capacidad_de_ahorro_descuenta_pagos_de_deuda(ctx):
    ahorro = kpis.kpi_capacidad_ahorro(ctx)
    # CLI-2: ingreso 9,000 - pagos 3,000; antes salia 9,000 (sin compras)
    assert _valor(ahorro, "CLI-2") == pytest.approx(6000)
    # CLI-1: 13,333 - 6,000
    assert _valor(ahorro, "CLI-1") == pytest.approx(40000 / 3 - 6000)


# --- Ventana de meses (config/features.yaml) --------------------------------


@pytest.fixture
def ctx_largo(spark):
    """CLI-1 con 15 meses de historial: 10,000 de nomina los primeros 3
    meses (jul-sep 2025) y 4,000 los 12 siguientes (oct 2025 - sep 2026).
    En los 3 meses viejos compra solo en 'viajes'; en los 12 recientes, en
    'comida'. Un mes = una nomina y una compra."""
    d = Decimal
    filas = []
    for i in range(15):
        anio, mes = (2025 + (6 + i) // 12, (6 + i) % 12 + 1)
        viejo = i < 3
        nomina = d("10000" if viejo else "4000")
        categoria = "viajes" if viejo else "comida"
        filas.append(
            (f"TX-{i}-N", "CLI-1", dt.date(anio, mes, 15), "nomina", nomina, "ingreso")
        )
        filas.append(
            (
                f"TX-{i}-C",
                "CLI-1",
                dt.date(anio, mes, 20),
                "compra",
                d("-1000"),
                categoria,
            )
        )
    tx = spark.createDataFrame(
        filas,
        "transaccion_id string, cliente_id string, fecha date, "
        "tipo_transaccion string, monto decimal(12,2), categoria string",
    )
    clientes = spark.createDataFrame([("CLI-1",)], "cliente_id string")
    return {"transacciones": tx, "clientes": clientes}


def test_la_ventana_deja_fuera_los_meses_viejos(ctx_largo):
    completo = kpis.kpi_ingreso_mensual_promedio(ctx_largo)
    con_ventana = kpis.kpi_ingreso_mensual_promedio({**ctx_largo, "ventana_meses": 12})

    assert _valor(completo, "CLI-1") == pytest.approx((3 * 10000 + 12 * 4000) / 15)
    assert _valor(con_ventana, "CLI-1") == pytest.approx(4000)


def test_la_ventana_aplica_a_categoria_y_frecuencia(ctx_largo):
    ctx = {**ctx_largo, "ventana_meses": 2}
    categoria = kpis.kpi_categoria_gasto_dominante(ctx).set_index("cliente_id")
    frecuencia = kpis.kpi_frecuencia_transacciones(ctx)

    assert categoria.loc["CLI-1", "valor_texto"] == "comida"
    assert _valor(frecuencia, "CLI-1") == pytest.approx(2)


def test_la_ventana_se_ancla_al_mes_mas_reciente(ctx_largo):
    """Con ventana de 1 mes queda solo septiembre de 2026, aunque el ultimo
    movimiento sea a mitad de mes."""
    tx = kpis.transacciones_en_ventana(ctx_largo["transacciones"], 1)
    meses = {r.fecha.strftime("%Y-%m") for r in tx.select("fecha").collect()}
    assert meses == {"2026-09"}


def test_con_historial_corto_la_ventana_no_cambia_nada(ctx):
    """El ctx base tiene 3 meses: con ventana de 12, cada KPI de flujo sale
    igual que sin ventana. Es lo que deja intacta a la sesion base de 12
    meses (y al modelo y la referencia del PSI construidos sobre ella)."""
    con_ventana = {**ctx, "ventana_meses": 12}
    for kpi in (
        kpis.kpi_ingreso_mensual_promedio,
        kpis.kpi_estabilidad_ingreso,
        kpis.kpi_gasto_mensual_total,
        kpis.kpi_capacidad_ahorro,
        kpis.kpi_actividad_p2p,
    ):
        sin = kpi(ctx).set_index("cliente_id")["valor_numerico"].sort_index()
        con = kpi(con_ventana).set_index("cliente_id")["valor_numerico"].sort_index()
        assert sin.tolist() == pytest.approx(con.tolist()), kpi.__name__


@pytest.mark.parametrize("valor", [0, -3, 1.5, "12", True])
def test_cargar_ventana_rechaza_valores_invalidos(tmp_path, valor):
    import yaml

    ruta = tmp_path / "features.yaml"
    ruta.write_text(yaml.safe_dump({"ventana_meses": valor}), encoding="utf-8")
    with pytest.raises(ValueError, match="ventana_meses"):
        kpis.cargar_ventana(ruta)


def test_config_del_repo_usa_la_ventana_de_la_sesion_base():
    raiz = Path(__file__).resolve().parents[1]
    import yaml

    meses_base = yaml.safe_load(open(raiz / "config/sesion.yaml"))["meses"]
    assert kpis.cargar_ventana(raiz / "config/features.yaml") == meses_base

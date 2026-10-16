"""
tests/test_escenario_deriva.py

Escenario de deriva del generador (src/common/escenario_deriva.py y su uso
en generate_synthetic_sources.py): apagado no cambia nada, activo es
reproducible y solo toca a los clientes afectados en los meses de entrega,
y el generador se niega a reescribir un mes ya entregado.

Volumen reducido (30 clientes, 2 meses base) para que corran rapido.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_synthetic_sources as generador  # noqa: E402
from src.common.escenario_deriva import (  # noqa: E402
    cargar_escenario,
    clientes_afectados,
    efectos,
    huella,
    meses_de_escenario,
)
from src.common.sesiones import es_continuacion  # noqa: E402

BASE = {
    "semilla": 42,
    "fecha_referencia": "2026-09-30",
    "clientes": 30,
    "meses": 2,
}
ESCENARIO = {
    "activo": True,
    "desde_entrega": 1,
    "proporcion_clientes": 0.5,
    "caida_ingreso_mensual": 0.10,
    "aumento_gasto_mensual": 0.05,
    "caida_puntualidad_mensual": 0.30,
}


def _generar(carpeta: Path, meses_adicionales: int, escenario: dict | None) -> Path:
    parametros = dict(BASE, meses_adicionales=meses_adicionales)
    if escenario:
        parametros["escenario_deriva"] = cargar_escenario(escenario).como_dict()
    generador.generar_fuentes(carpeta, parametros)
    return carpeta


def _mes(carpeta: Path, anio: int, mes: int) -> pd.DataFrame:
    return pd.read_csv(
        carpeta / "transacciones" / f"transacciones_{anio}_{mes:02d}.csv"
    )


def _bytes(carpeta: Path) -> dict:
    return {
        str(p.relative_to(carpeta)): p.read_bytes()
        for p in sorted(carpeta.rglob("*"))
        if p.is_file()
    }


# --- Configuracion ----------------------------------------------------------


def test_apagado_o_ausente_no_es_escenario():
    assert cargar_escenario(None) is None
    assert cargar_escenario(dict(ESCENARIO, activo=False)) is None


@pytest.mark.parametrize(
    "cambio, mensaje",
    [
        ({"desde_entrega": 0}, "desde_entrega"),
        ({"proporcion_clientes": 0.0}, "proporcion_clientes"),
        ({"caida_ingreso_mensual": 1.0}, "caida_ingreso_mensual"),
        ({"aumento_gasto_mensual": float("nan")}, "finito"),
        ({"caida_puntualidad_mensual": -0.1}, "caida_puntualidad_mensual"),
    ],
)
def test_config_invalida_se_rechaza(cambio, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        cargar_escenario(dict(ESCENARIO, **cambio))


def test_huella_distingue_escenarios_y_el_id_la_lleva():
    a = cargar_escenario(ESCENARIO)
    b = cargar_escenario(dict(ESCENARIO, caida_ingreso_mensual=0.2))
    assert huella(a) == huella(cargar_escenario(dict(ESCENARIO)))
    assert huella(a) != huella(b)
    fecha = pd.Timestamp("2026-09-30").date()
    assert generador.id_sesion(42, fecha, 30, 2, 1, huella(a)) == (
        f"s42_20260930_30c_2m_e1_d{huella(a)}"
    )
    assert generador.id_sesion(42, fecha, 30, 2, 1) == "s42_20260930_30c_2m_e1"


def test_meses_de_escenario_no_tocan_los_meses_base():
    escenario = cargar_escenario(dict(ESCENARIO, desde_entrega=2))
    base = (2026, 9)
    assert meses_de_escenario(escenario, 2026, 9, base) == 0  # ultimo mes base
    assert meses_de_escenario(escenario, 2026, 10, base) == 0  # entrega 1
    assert meses_de_escenario(escenario, 2026, 11, base) == 1  # entrega 2
    assert meses_de_escenario(escenario, 2027, 1, base) == 3


def test_efectos_compuestos():
    escenario = cargar_escenario(ESCENARIO)
    assert efectos(escenario, 2)["factor_ingreso"] == pytest.approx(0.9**2)
    assert efectos(escenario, 2)["factor_gasto"] == pytest.approx(1.05**2)
    assert efectos(escenario, 2)["caida_puntualidad"] == pytest.approx(0.6)


def test_sorteo_de_afectados_reproducible_y_sin_estado_global():
    import numpy as np

    estado = np.random.get_state()[1].copy()
    ids = [f"CLI-{i:06d}" for i in range(100)]
    a = clientes_afectados(ids, cargar_escenario(ESCENARIO), 42)
    b = clientes_afectados(list(reversed(ids)), cargar_escenario(ESCENARIO), 42)
    assert a == b and len(a) == 50
    assert (np.random.get_state()[1] == estado).all()


# --- Generacion -------------------------------------------------------------


def test_apagado_es_identico_byte_a_byte(tmp_path):
    sin = _generar(tmp_path / "sin", 1, None)
    # Activo pero empezando despues del ultimo mes generado: ningun mes afectado.
    tarde = _generar(tmp_path / "tarde", 1, dict(ESCENARIO, desde_entrega=5))
    assert _bytes(sin) == _bytes(tarde)


def test_activo_es_reproducible(tmp_path):
    a = _generar(tmp_path / "a", 2, ESCENARIO)
    b = _generar(tmp_path / "b", 2, ESCENARIO)
    assert _bytes(a) == _bytes(b)


def test_meses_base_y_entidades_no_cambian(tmp_path):
    sin = _bytes(_generar(tmp_path / "sin", 2, None))
    con = _bytes(_generar(tmp_path / "con", 2, ESCENARIO))
    for nombre in sin:
        if nombre.startswith("transacciones/transacciones_2026_1"):
            continue  # entregas: 2026_10 y 2026_11
        assert sin[nombre] == con[nombre], nombre
    assert (
        sin["transacciones/transacciones_2026_10.csv"]
        != con["transacciones/transacciones_2026_10.csv"]
    )


def test_en_las_entregas_solo_cambian_los_afectados(tmp_path):
    sin = _mes(_generar(tmp_path / "sin", 2, None), 2026, 11)
    con = _mes(_generar(tmp_path / "con", 2, ESCENARIO), 2026, 11)
    clientes = pd.read_csv(tmp_path / "con" / "clientes.csv")["cliente_id"]
    afectados = clientes_afectados(clientes, cargar_escenario(ESCENARIO), 42)

    # No afectados: filas identicas, ids incluidos (el hueco del contador
    # evita que un pago suprimido recorra la numeracion).
    pd.testing.assert_frame_equal(
        sin[~sin["cliente_id"].isin(afectados)].reset_index(drop=True),
        con[~con["cliente_id"].isin(afectados)].reset_index(drop=True),
    )

    # Afectados: mismas filas salvo pagos de deuda (menos o iguales).
    columnas = [
        "transaccion_id",
        "cliente_id",
        "fecha",
        "tipo_transaccion",
        "categoria",
    ]
    sin_a = sin[sin["cliente_id"].isin(afectados)]
    con_a = con[con["cliente_id"].isin(afectados)]
    no_pago = ~sin_a["tipo_transaccion"].eq("pago_recurrente")
    pd.testing.assert_frame_equal(
        sin_a[no_pago][columnas].reset_index(drop=True),
        con_a[~con_a["tipo_transaccion"].eq("pago_recurrente")][columnas].reset_index(
            drop=True
        ),
    )
    pagos_sin = (sin_a["tipo_transaccion"] == "pago_recurrente").sum()
    pagos_con = (con_a["tipo_transaccion"] == "pago_recurrente").sum()
    assert pagos_con < pagos_sin

    # Nomina de los formales afectados: 2 meses de escenario, factor 0.9^2.
    nomina = lambda df: df[df["tipo_transaccion"] == "nomina"].set_index(  # noqa: E731
        "transaccion_id"
    )["monto"]
    comunes = nomina(sin_a).index
    assert len(comunes) > 0
    assert nomina(con_a).loc[comunes].values == pytest.approx(
        nomina(sin_a).loc[comunes].values * 0.81, abs=0.02
    )


# --- Regla de continuacion (main) -------------------------------------------


def _config(tmp_path: Path, nombre: str, escenario: dict | None) -> Path:
    ruta = tmp_path / f"{nombre}.yaml"
    contenido = dict(BASE)
    if escenario is not None:
        contenido["escenario_deriva"] = escenario
    ruta.write_text(yaml.safe_dump(contenido), encoding="utf-8")
    return ruta


def _main(tmp_path: Path, config: Path, entregas: int):
    generador.main(
        [
            "--config",
            str(config),
            "--meses-adicionales",
            str(entregas),
            "--sesiones-dir",
            str(tmp_path / "sesiones"),
            "--out",
            str(tmp_path / "activa"),
        ]
    )


def _activa(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "activa" / "manifest.json").read_text())


def test_cambiar_el_escenario_tras_entregar_meses_afectados_se_rechaza(tmp_path):
    _main(tmp_path, _config(tmp_path, "a", ESCENARIO), 1)
    antes = _activa(tmp_path)
    otro = dict(ESCENARIO, caida_ingreso_mensual=0.20)

    with pytest.raises(SystemExit, match="transacciones/transacciones_2026_10.csv"):
        _main(tmp_path, _config(tmp_path, "b", otro), 2)

    assert _activa(tmp_path) == antes, "la sesion activa no debe cambiar"
    assert not list((tmp_path / "sesiones").glob(".*")), "no deben quedar temporales"


def test_activar_el_escenario_sin_tocar_lo_entregado_esta_permitido(tmp_path):
    _main(tmp_path, _config(tmp_path, "sin", None), 1)
    _main(tmp_path, _config(tmp_path, "con", dict(ESCENARIO, desde_entrega=2)), 2)

    activa = _activa(tmp_path)
    assert activa["parametros"]["meses_adicionales"] == 2
    assert activa["sesion_id"].endswith(
        huella(cargar_escenario(ESCENARIO | {"desde_entrega": 2}))
    )


def test_cambiar_a_otra_base_esta_permitido(tmp_path):
    _main(tmp_path, _config(tmp_path, "a", ESCENARIO), 1)
    otra_base = tmp_path / "otra.yaml"
    otra_base.write_text(
        yaml.safe_dump(dict(BASE, clientes=20, escenario_deriva=ESCENARIO)),
        encoding="utf-8",
    )
    _main(tmp_path, otra_base, 1)
    assert _activa(tmp_path)["parametros"]["clientes"] == 20


def test_continuacion_ignora_el_escenario_y_el_id():
    activa = dict(BASE, meses_adicionales=1, escenario_deriva={"x": 1})
    assert es_continuacion(activa, dict(BASE, meses_adicionales=2))
    assert es_continuacion(
        activa, dict(BASE, meses_adicionales=1, escenario_deriva={"x": 2})
    )
    assert not es_continuacion(activa, dict(BASE, meses_adicionales=0))
    assert not es_continuacion(activa, dict(BASE, clientes=31, meses_adicionales=2))

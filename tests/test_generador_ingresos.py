"""
tests/test_generador_ingresos.py

Ingreso declarado por ocupacion (config/ingresos.yaml) en el generador
sintetico: que la configuracion este completa, que se respete el piso del
salario minimo, que el ingreso dependa de la ocupacion y que cambiar el
modelo de ingresos no mueva ningun otro dato del cliente.
"""

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_synthetic_sources as g  # noqa: E402

FECHA = date(2026, 9, 30)


def clientes(n, modelo=None, semilla=42):
    g.sembrar(semilla)
    return g.generar_clientes(n, FECHA, modelo)


def test_la_configuracion_cubre_todas_las_ocupaciones():
    modelo = g.cargar_modelo_ingresos()
    assert set(modelo["ocupaciones"]) == set(g.OCUPACIONES)
    assert modelo["salario_minimo_mensual"] == pytest.approx(
        315.04 * 365 / 12, abs=0.01
    )


def test_configuracion_incompleta_falla(tmp_path):
    modelo = g.cargar_modelo_ingresos()
    del modelo["ocupaciones"]["Estudiante"]
    ruta = tmp_path / "ingresos.yaml"
    ruta.write_text(yaml.safe_dump(modelo), encoding="utf-8")
    with pytest.raises(ValueError, match="Estudiante"):
        g.cargar_modelo_ingresos(ruta)


def test_piso_de_salario_minimo():
    modelo = g.cargar_modelo_ingresos()
    df = clientes(2000, modelo)
    con_piso = [o for o, p in modelo["ocupaciones"].items() if p["piso_salario_minimo"]]
    minimo = df[df["ocupacion"].isin(con_piso)]["ingreso_mensual_declarado"].min()
    assert minimo >= modelo["salario_minimo_mensual"] - 0.01


def test_el_ingreso_depende_de_la_ocupacion():
    medianas = clientes(3000).groupby("ocupacion")["ingreso_mensual_declarado"].median()
    assert (
        medianas["Estudiante"]
        < medianas["Comerciante"]
        < medianas["Empleado"]
        < medianas["Profesionista"]
        < medianas["Empresario"]
    )


def test_cambiar_el_modelo_solo_cambia_el_ingreso():
    modelo = g.cargar_modelo_ingresos()
    otro = yaml.safe_load(yaml.safe_dump(modelo))
    for p in otro["ocupaciones"].values():
        p["mediana"] *= 2
    a, b = clientes(200, modelo), clientes(200, otro)
    otras = [c for c in a.columns if c != "ingreso_mensual_declarado"]
    assert a[otras].equals(b[otras])
    assert (
        b["ingreso_mensual_declarado"] > a["ingreso_mensual_declarado"]
    ).mean() > 0.9


def test_el_modelo_queda_en_los_parametros_de_la_sesion():
    args = SimpleNamespace(
        config=None,
        semilla=None,
        fecha_referencia="2026-09-30",
        clientes=None,
        meses=None,
        meses_adicionales=None,
    )
    parametros = g.resolver_parametros(args)
    assert parametros["modelo_ingresos"] == g.cargar_modelo_ingresos()

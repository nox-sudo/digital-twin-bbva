"""
tests/test_bronze_estructura.py

Lineas de CSV mal formadas (hallazgo 5 de la auditoria del 2026-10-14):
Bronze las conserva con la linea original en _corrupt_record en vez de
recortarlas o rellenarlas en silencio (ingest_bronze.leer_csv), y Silver
las manda a cuarentena con la regla "estructura" sin llevar la linea en
claro (src/calidad/registro.marcar_estructura).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest_bronze import COLUMNA_CORRUPTA, leer_csv, leer_entidad  # noqa: E402
from src.calidad.registro import incidencias, marcar_estructura  # noqa: E402
from src.silver.validation import validate_entity  # noqa: E402

CLIENTES = (
    "cliente_id,nombre,email\n"
    "C1,Ana,a@example.com\n"
    "C2,Luis,l@example.com,EXTRA\n"  # columna de mas
    "C3,Eva\n"  # columna de menos
    'C4,"Sin cerrar,e@example.com\n'  # comilla sin cerrar
    "C5,Ok,o@example.com\n"
)


@pytest.fixture
def csv_clientes(tmp_path):
    ruta = tmp_path / "clientes.csv"
    ruta.write_text(CLIENTES, encoding="utf-8")
    return ruta


def _por_id(df):
    return {f["cliente_id"]: f for f in df.collect()}


def test_las_lineas_mal_formadas_conservan_su_texto(spark, csv_clientes):
    df = leer_csv(spark, [csv_clientes]).cache()
    filas = _por_id(df)

    assert {c for c, f in filas.items() if f[COLUMNA_CORRUPTA]} == {"C2", "C3", "C4"}
    assert filas["C2"][COLUMNA_CORRUPTA] == "C2,Luis,l@example.com,EXTRA"
    assert filas["C3"][COLUMNA_CORRUPTA] == "C3,Eva"


def test_las_lineas_bien_formadas_no_cambian(spark, csv_clientes):
    filas = _por_id(leer_csv(spark, [csv_clientes]).cache())

    assert filas["C1"][COLUMNA_CORRUPTA] is None
    assert (filas["C5"]["nombre"], filas["C5"]["email"]) == ("Ok", "o@example.com")


def test_el_esquema_sale_del_encabezado_y_admite_columnas_nuevas(spark, tmp_path):
    """Una entrega con una columna nueva entra igual: se lee con su propio
    encabezado y se une por nombre con la anterior."""
    viejo = tmp_path / "a" / "clientes.csv"
    nuevo = tmp_path / "b" / "clientes.csv"
    viejo.parent.mkdir()
    nuevo.parent.mkdir()
    viejo.write_text("cliente_id,nombre\nC1,Ana\n", encoding="utf-8")
    nuevo.write_text("cliente_id,nombre,curp\nC2,Luis,LUXX\n", encoding="utf-8")

    df, formato = leer_entidad(spark, "clientes", [viejo, nuevo])
    filas = _por_id(df.cache())

    assert formato == "csv"
    assert set(df.columns) == {"cliente_id", "nombre", "curp", COLUMNA_CORRUPTA}
    assert filas["C1"]["curp"] is None and filas["C2"]["curp"] == "LUXX"
    assert all(f[COLUMNA_CORRUPTA] is None for f in filas.values())


def test_csv_sin_encabezado_falla_con_mensaje(spark, tmp_path):
    vacio = tmp_path / "clientes.csv"
    vacio.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="sin encabezado"):
        leer_csv(spark, [vacio])


def test_silver_manda_a_cuarentena_sin_la_linea_en_claro(spark, csv_clientes):
    df = marcar_estructura(leer_csv(spark, [csv_clientes]).cache())

    assert COLUMNA_CORRUPTA not in df.columns
    valido, cuarentena, reporte = validate_entity(df, "clientes", {})

    assert {f.cliente_id for f in valido.collect()} == {"C1", "C5"}
    assert {f.cliente_id for f in cuarentena.collect()} == {"C2", "C3", "C4"}
    assert reporte.violaciones_por_regla == {"estructura_registro": 3}
    assert COLUMNA_CORRUPTA not in cuarentena.columns


def test_la_incidencia_registra_la_regla_y_no_el_valor(spark, csv_clientes):
    df = marcar_estructura(leer_csv(spark, [csv_clientes]).cache())
    _, cuarentena, _ = validate_entity(df, "clientes", {})

    registro = incidencias(cuarentena, "clientes", "cliente_id", "silver_test")
    filas = registro.collect()

    assert {(f.regla, f.columna) for f in filas} == {("estructura", "registro")}
    assert {f.llave for f in filas} == {"C2", "C3", "C4"}
    texto = " ".join(str(v) for f in filas for v in f.asDict().values())
    assert "example.com" not in texto and "EXTRA" not in texto


def test_tabla_sin_columna_corrupta_pasa_igual(spark):
    df = spark.createDataFrame([("C1", "Ana")], ["cliente_id", "nombre"])
    assert marcar_estructura(df).columns == ["cliente_id", "nombre"]

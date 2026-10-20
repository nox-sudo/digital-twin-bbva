"""
tests/test_dashboard_datos.py

Capa de datos del dashboard (dashboard/datos.py). No importa Streamlit:
trabaja sobre un Gold minimo en DuckDB y logs JSON de prueba.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard import datos  # noqa: E402

BANDAS = [
    {"nombre": "bajo", "desde": 0.0},
    {"nombre": "medio", "desde": 0.10},
    {"nombre": "alto", "desde": 0.30},
]


@pytest.fixture
def gold(tmp_path):
    ruta = str(tmp_path / "kpis.duckdb")
    features = pd.DataFrame(
        {
            "cliente_id": ["CLI-1", "CLI-2", "CLI-3"],
            "ingreso_mensual_promedio": [20000.0, 30000.0, 10000.0],
            "capacidad_ahorro": [5000.0, -1000.0, 200.0],
            "fecha_corte": [dt.date(2026, 9, 30)] * 3,
        }
    )
    kpis = pd.DataFrame(
        {
            "cliente_id": ["CLI-1", "CLI-2", "CLI-3"],
            "kpi_id": ["probabilidad_impago"] * 3,
            "valor_numerico": [0.05, 0.10, None],
        }
    )
    movimientos = pd.DataFrame(
        {
            "cliente_id": ["CLI-1"] * 4,
            "mes": ["2026-08", "2026-08", "2026-09", "2026-09"],
            "tipo_transaccion": ["nomina", "compra", "nomina", "compra"],
            "categoria": ["ingreso", "comida", "ingreso", "ropa"],
            "monto_total": [20000.0, -15000.0, 20000.0, -25000.0],
            "n_movimientos": [2, 10, 2, 12],
        }
    )
    con = duckdb.connect(ruta)
    for nombre, df in [
        ("gold_features_cliente", features),
        ("gold_kpis", kpis),
        ("gold_movimientos_mensuales", movimientos),
    ]:
        con.register("df", df)
        con.execute(f"CREATE TABLE {nombre} AS SELECT * FROM df")
        con.unregister("df")
    con.close()
    return ruta


def test_bandas_incluyen_el_limite_inferior_y_marcan_sin_score():
    probabilidad = pd.Series([0.0, 0.0999, 0.10, 0.30, 0.95, None])
    bandas = datos.asignar_banda(probabilidad, BANDAS).tolist()
    assert bandas == ["bajo", "bajo", "medio", "alto", "alto", "sin score"]


def test_portafolio_une_features_y_score(gold):
    df = datos.portafolio(gold, BANDAS)
    assert len(df) == 3
    assert df.set_index("cliente_id").loc["CLI-2", "banda_riesgo"] == "medio"
    resumen = datos.resumen_portafolio(df)
    assert resumen["clientes_sin_score"] == 1
    assert resumen["pct_ahorro_negativo"] == pytest.approx(1 / 3)
    assert resumen["probabilidad_media"] == pytest.approx(0.075)


def test_flujo_separa_entradas_y_salidas(gold):
    flujo = datos.flujo_mensual(gold, "CLI-1").set_index("mes")
    assert flujo.loc["2026-08", "entradas"] == 20000.0
    assert flujo.loc["2026-08", "salidas"] == 15000.0
    assert flujo.loc["2026-09", "neto"] == -5000.0


def test_salidas_por_categoria_suman_uno(gold):
    categorias = datos.salidas_por_categoria(gold, "CLI-1")
    assert categorias.iloc[0]["categoria"] == "ropa"
    assert categorias["proporcion"].sum() == pytest.approx(1.0)


def test_consistencia_detecta_clientes_sin_score(gold):
    chequeos = datos.consistencia_gold(gold).set_index("comprobacion")
    assert not chequeos.loc["Clientes con probabilidad de impago", "ok"]
    assert chequeos.loc["Fechas de corte distintas en el feature store", "ok"]


def test_gold_inexistente_da_error_claro(tmp_path):
    with pytest.raises(FileNotFoundError, match="Corre el pipeline"):
        datos.portafolio(str(tmp_path / "no_existe.duckdb"), BANDAS)


def test_corridas_normaliza_los_dos_formatos_de_log(tmp_path):
    (tmp_path / "bronze_ingestion_20261016_100000.json").write_text(
        json.dumps(
            {
                "inicio": "2026-10-16 10:00:00",
                "duracion_segundos": 18.0,
                "estatus": "SUCCESS",
            }
        )
    )
    (tmp_path / "silver_run_20261016_100100.json").write_text(
        json.dumps(
            {
                "layer": "silver",
                "inicio": "2026-10-16T10:01:00",
                "duracion_total_segundos": 45.0,
                "status": "FAILED: no se pudo leer transacciones",
                "entities": {
                    "clientes": {
                        "filas_cuarentena": 2,
                        "violaciones_por_regla": {"notnull_curp": 2},
                    }
                },
            }
        )
    )
    (tmp_path / "gold_run_20261016_100200.json").write_text("{no es json")

    corridas = datos.corridas(str(tmp_path)).set_index("capa")
    assert corridas.loc["bronze", "estatus"] == "SUCCESS"
    assert corridas.loc["silver", "estatus"] == "FAILED"
    assert corridas.loc["silver", "duracion_segundos"] == 45.0
    assert corridas.loc["gold", "estatus"] == "LOG ILEGIBLE"
    # Dentro de cada capa, la ultima fila es el archivo mas reciente,
    # aunque su log este danado y no tenga hora de inicio.
    ordenadas = datos.corridas(str(tmp_path))
    assert ordenadas[ordenadas["capa"] == "gold"].iloc[-1]["estatus"] == "LOG ILEGIBLE"

    entidades = datos.entidades_ultima_corrida(str(tmp_path), "silver")
    assert entidades.iloc[0]["reglas_violadas"] == "notnull_curp (2)"


def _corrida_psi(
    carpeta: Path, run_id: str, corrida: str, corte: str, ref: str, psi: float
):
    carpeta.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "run_id": [run_id] * 2,
            "fecha_corrida": [pd.Timestamp(corrida)] * 2,
            "fecha_corte": [dt.date.fromisoformat(corte)] * 2,
            "referencia_id": [ref] * 2,
            "variable": ["capacidad_ahorro", "probabilidad_impago"],
            "tipo": ["feature", "score"],
            "psi": [psi, psi / 2],
            "estado": ["estable", "estable"],
            "n_referencia": [500] * 2,
            "n_actual": [500] * 2,
        }
    ).to_parquet(carpeta / f"{run_id}.parquet", index=False)


def test_deriva_psi_vacio_sin_corridas(tmp_path):
    assert datos.deriva_psi(str(tmp_path / "no-existe")).empty
    assert list(datos.deriva_psi(str(tmp_path)).columns) == datos.COLUMNAS_DERIVA


def test_tendencia_usa_la_referencia_activa_y_la_ultima_corrida_por_corte(tmp_path):
    h = tmp_path / "deriva_psi"
    _corrida_psi(h, "r0", "2026-10-01 10:00", "2026-09-30", "ref_vieja", 0.9)
    _corrida_psi(h, "r1", "2026-10-02 10:00", "2026-09-30", "ref_nueva", 0.0)
    _corrida_psi(h, "r2", "2026-10-02 11:00", "2026-09-30", "ref_nueva", 0.01)
    _corrida_psi(h, "r3", "2026-11-02 10:00", "2026-10-31", "ref_nueva", 0.2)

    tendencia = datos.tendencia_psi(datos.deriva_psi(str(h)))

    assert set(tendencia["referencia_id"]) == {"ref_nueva"}
    assert set(tendencia["run_id"]) == {"r2", "r3"}
    capacidad = tendencia[tendencia["variable"] == "capacidad_ahorro"]
    assert capacidad["psi"].tolist() == [0.01, 0.2]


# --- Ventana en la vista Cliente y comparacion contra la base --------------


def test_inicio_ventana_cruza_el_anio():
    assert datos.inicio_ventana("2027-04", 12) == "2026-05"
    assert datos.inicio_ventana("2026-09", 1) == "2026-09"


def test_salidas_por_categoria_respeta_la_ventana(gold):
    todo = datos.salidas_por_categoria(gold, "CLI-1").set_index("categoria")
    ultimo_mes = datos.salidas_por_categoria(gold, "CLI-1", 1).set_index("categoria")

    assert set(todo.index) == {"comida", "ropa"}
    assert set(ultimo_mes.index) == {"ropa"}  # septiembre: solo ropa
    assert ultimo_mes.loc["ropa", "proporcion"] == pytest.approx(1.0)


def _resumen_en(carpeta, corte, pct, corrida):
    from src.gold.resumen_portafolio import escribir_historico

    escribir_historico(
        {
            "clientes": 500,
            "ingreso_mediano": 15000.0,
            "pct_ahorro_negativo": pct,
            "probabilidad_media": 0.2,
            "fecha_corte": dt.date.fromisoformat(corte),
        },
        carpeta,
        ahora=dt.datetime.fromisoformat(corrida),
    )


def test_historico_del_resumen_toma_la_ultima_corrida_de_cada_corte(tmp_path):
    from src.gold.resumen_portafolio import cargar_historico

    h = tmp_path / "resumen"
    _resumen_en(h, "2026-09-30", 0.20, "2026-10-01T10:00:00")
    _resumen_en(h, "2026-09-30", 0.23, "2026-10-01T11:00:00")
    _resumen_en(h, "2026-10-31", 0.30, "2026-11-01T10:00:00")

    historico = cargar_historico(h)
    assert len(historico) == 2
    assert historico["pct_ahorro_negativo"].tolist() == pytest.approx([0.23, 0.30])


def test_resumen_base_es_el_corte_mas_antiguo(tmp_path):
    h = tmp_path / "resumen"
    assert datos.resumen_base(str(h), dt.date(2026, 10, 31)) is None  # sin historico

    _resumen_en(h, "2026-09-30", 0.23, "2026-10-01T10:00:00")
    _resumen_en(h, "2026-10-31", 0.30, "2026-11-01T10:00:00")

    base = datos.resumen_base(str(h), dt.date(2026, 10, 31))
    assert base["pct_ahorro_negativo"] == pytest.approx(0.23)
    # En la propia base no hay contra que comparar.
    assert datos.resumen_base(str(h), dt.date(2026, 9, 30)) is None


def test_resumen_del_dashboard_y_del_historico_usan_la_misma_definicion(gold):
    from src.gold.resumen_portafolio import resumen

    df = datos.portafolio(gold, BANDAS)
    assert datos.resumen_portafolio(df) == resumen(df)

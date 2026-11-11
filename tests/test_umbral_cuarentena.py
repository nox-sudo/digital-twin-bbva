"""
tests/test_umbral_cuarentena.py

Umbral de cuarentena de Silver (src/calidad/umbrales.py) y su efecto en
transform_silver.process_entity: sobre el aviso se registra una alerta y
Silver se escribe; sobre el bloqueo la alerta se registra, la cuarentena se
escribe y Silver no, y la entidad falla.

La integracion usa Spark real con un escritor falso (sin Delta, como el resto
de la suite; ver conftest.py) que registra las rutas guardadas.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import yaml  # noqa: E402

import transform_silver  # noqa: E402
from src.calidad import umbrales as um  # noqa: E402
from src.common.logging_utils import PipelineRunLogger  # noqa: E402

BASE = um.Umbral(aviso=0.01, bloqueo=0.10, filas_minimas=100)


# --- Logica pura ---------------------------------------------------------------


@pytest.mark.parametrize(
    "entrada, cuarentena, nivel",
    [
        (1000, 0, um.OK),
        (1000, 9, um.OK),  # 0.9 %
        (1000, 10, um.AVISO),  # 1 % exacto: alcanza el aviso
        (1000, 99, um.AVISO),
        (1000, 100, um.BLOQUEO),  # 10 % exacto: alcanza el bloqueo
        (1000, 1000, um.BLOQUEO),
        (0, 0, um.OK),  # entidad vacia: sin division entre cero
    ],
)
def test_niveles(entrada, cuarentena, nivel):
    assert um.evaluar("e", entrada, cuarentena, {}, BASE).nivel == nivel


def test_pocas_filas_avisan_pero_no_bloquean():
    # 1 de 8 es 12.5 %: sobre el bloqueo, pero con 8 filas no dice nada.
    assert um.evaluar("e", 8, 1, {}, BASE).nivel == um.AVISO
    assert um.evaluar("e", 100, 50, {}, BASE).nivel == um.BLOQUEO


def test_bloqueo_nulo_solo_avisa():
    umbral = um.Umbral(aviso=0.01, bloqueo=None, filas_minimas=0)
    assert um.evaluar("e", 100, 100, {}, umbral).nivel == um.AVISO


def test_reglas_principales_ordenadas_y_sin_valores():
    ev = um.evaluar(
        "clientes",
        1000,
        200,
        {"pattern_curp": 5, "not_null_nombre": 190, "fk_x": 5, "unique_rfc": 1},
        BASE,
    )
    assert ev.reglas_principales == [
        ("not_null_nombre", 190),
        ("fk_x", 5),
        ("pattern_curp", 5),
    ]
    registro = um.registro_de_alerta(ev, "silver_x")
    assert registro["nivel"] == um.BLOQUEO
    assert registro["tasa"] == 0.2
    assert set(registro) == {
        "fecha",
        "run_id",
        "entidad",
        "nivel",
        "filas_entrada",
        "filas_cuarentena",
        "tasa",
        "umbral_aviso",
        "umbral_bloqueo",
        "reglas_principales",
    }


def test_carga_default_y_reemplazo_por_entidad():
    reglas = {
        "umbrales_cuarentena": {
            "default": {"aviso": 0.01, "bloqueo": 0.1, "filas_minimas": 100},
            "transacciones": {"bloqueo": 0.05},
        }
    }
    u = um.cargar_umbrales(reglas)
    assert um.umbral_de("clientes", u) == BASE
    assert um.umbral_de("transacciones", u) == um.Umbral(0.01, 0.05, 100)


@pytest.mark.parametrize(
    "seccion",
    [
        None,
        {"clientes": {"aviso": 0.01}},  # sin default
        {"default": {"aviso": 0.2, "bloqueo": 0.1, "filas_minimas": 0}},
        {"default": {"aviso": 0, "bloqueo": 0.1, "filas_minimas": 0}},
        {"default": {"aviso": 0.01, "bloqueo": 1.5, "filas_minimas": 0}},
        {"default": {"aviso": 0.01, "bloqueo": 0.1, "filas_minimas": -1}},
        {
            "default": {"aviso": 0.01, "bloqueo": 0.1, "filas_minimas": 0},
            "clientes": {"avisos": 0.02},  # clave mal escrita
        },
    ],
)
def test_configuracion_invalida_falla(seccion):
    with pytest.raises(ValueError, match="umbrales_cuarentena"):
        um.cargar_umbrales({"umbrales_cuarentena": seccion} if seccion else {})


def test_configuracion_del_repo_es_valida():
    with open(RAIZ / "config" / "business_rules.yaml", encoding="utf-8") as f:
        reglas = yaml.safe_load(f)
    u = um.cargar_umbrales(reglas)
    assert um.umbral_de("clientes", u) == BASE
    # El motor de validacion busca reglas por entidad: la seccion nueva no
    # debe parecer una entidad del orden de procesamiento.
    assert "umbrales_cuarentena" not in transform_silver.PROCESSING_ORDER


# --- Integracion con process_entity --------------------------------------------


class _Escritor:
    def __init__(self, guardadas: list):
        self.guardadas = guardadas

    def mode(self, *_):
        return self

    def format(self, *_):
        return self

    def option(self, *_):
        return self

    def save(self, ruta):
        self.guardadas.append(str(ruta))


class _SparkFalso:
    """Solo lo que process_entity usa para leer Bronze."""

    def __init__(self, df):
        self._df = df

    @property
    def read(self):
        df = self._df

        class _Lector:
            def format(self, *_):
                return self

            def load(self, *_):
                return df

        return _Lector()


REGLAS = {
    "prueba": {"primary_key": "id", "not_null": ["valor"]},
    "umbrales_cuarentena": {
        "default": {"aviso": 0.01, "bloqueo": 0.10, "filas_minimas": 100}
    },
}


def _correr(spark, tmp_path, monkeypatch, nulos: int):
    # Bronze trae _ingestion_timestamp; la deduplicacion ordena por ella.
    llegada = datetime(2026, 11, 1)
    filas = [(f"ID-{i:04d}", None if i < nulos else "x", llegada) for i in range(200)]
    df = spark.createDataFrame(
        filas, "id string, valor string, _ingestion_timestamp timestamp"
    )
    guardadas: list[str] = []
    monkeypatch.setattr(type(df), "write", property(lambda _self: _Escritor(guardadas)))
    monkeypatch.setitem(transform_silver.PRIMARY_KEYS, "prueba", "id")
    calidad = tmp_path / "calidad"
    logger = PipelineRunLogger(layer="silver", log_dir=str(tmp_path / "logs"))
    kwargs = dict(
        spark=_SparkFalso(df),
        entity_name="prueba",
        bronze_path="b",
        silver_path="silver",
        quarantine_path=str(tmp_path / "cuarentena"),
        rules=REGLAS,
        silver_context={},
        logger=logger,
        politica_pii={},
        sal_pii="sal",
        calidad_path=str(calidad),
        run_id="silver_prueba",
        umbrales=um.cargar_umbrales(REGLAS),
    )
    return kwargs, guardadas, calidad / um.ARCHIVO_ALERTAS, logger


def _alertas(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    return [json.loads(x) for x in ruta.read_text(encoding="utf-8").splitlines()]


def test_entrega_limpia_sin_alerta(spark, tmp_path, monkeypatch):
    kwargs, guardadas, alertas, _ = _correr(spark, tmp_path, monkeypatch, nulos=0)
    transform_silver.process_entity(**kwargs)
    assert "silver/prueba" in guardadas
    assert _alertas(alertas) == []


def test_aviso_escribe_silver_y_registra_alerta(spark, tmp_path, monkeypatch):
    kwargs, guardadas, alertas, logger = _correr(
        spark, tmp_path, monkeypatch, nulos=4
    )  # 2 %
    transform_silver.process_entity(**kwargs)
    assert "silver/prueba" in guardadas
    [alerta] = _alertas(alertas)
    assert alerta["nivel"] == um.AVISO
    assert (alerta["filas_entrada"], alerta["filas_cuarentena"]) == (200, 4)
    assert alerta["reglas_principales"] == [{"regla": "not_null_valor", "filas": 4}]
    assert logger.entities["prueba"]["nivel_cuarentena"] == um.AVISO


def test_bloqueo_no_escribe_silver_y_falla(spark, tmp_path, monkeypatch):
    kwargs, guardadas, alertas, logger = _correr(
        spark, tmp_path, monkeypatch, nulos=30
    )  # 15 %
    with pytest.raises(um.CuarentenaExcedida, match="30 de 200"):
        transform_silver.process_entity(**kwargs)
    assert "silver/prueba" not in guardadas
    # La cuarentena y el historico si: explican por que se detuvo.
    assert str(tmp_path / "cuarentena" / "prueba") in guardadas
    assert any(r.endswith("incidencias") for r in guardadas)
    [alerta] = _alertas(alertas)
    assert alerta["nivel"] == um.BLOQUEO
    assert logger.entities["prueba"]["estatus"] == "FAILED"


# --- Vista de Observabilidad con alertas ---------------------------------------


def test_observabilidad_muestra_las_alertas(tmp_path, monkeypatch):
    st_testing = pytest.importorskip("streamlit.testing.v1")
    import streamlit as st

    sys.path.insert(0, str(RAIZ / "tests"))
    from datos_asistente import config_vistas
    from dashboard import comun
    from src.common.usuarios import Usuario

    cfg = config_vistas(tmp_path)
    for cuarentena in (20, 300):
        ev = um.evaluar("transacciones", 1000, cuarentena, {"fk_cuenta_id": 7}, BASE)
        um.escribir_alerta(
            um.registro_de_alerta(ev, "silver_x"), cfg["alertas_cuarentena"]
        )
    monkeypatch.setattr(comun, "config", lambda: cfg)
    st.cache_data.clear()
    at = st_testing.AppTest.from_file(
        str(RAIZ / "dashboard" / "paginas" / "observabilidad.py")
    )
    at.session_state["usuario"] = Usuario("admin", "admin")
    at.run(timeout=60)
    assert not at.exception, [e.value for e in at.exception]
    assert "Alertas de cuarentena" in [s.value for s in at.subheader]
    tabla = next(
        d.value for d in at.dataframe if "entidad" in getattr(d.value, "columns", [])
    )
    assert sorted(tabla["nivel"]) == ["aviso", "bloqueo"]

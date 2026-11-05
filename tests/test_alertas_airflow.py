"""
tests/test_alertas_airflow.py

Alerta por fallo del DAG (dags/alertas.py). El modulo no importa Airflow:
el contexto de la tarea se arma con objetos falsos que tienen los mismos
atributos que lee el callback.
"""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

RAIZ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("alertas", RAIZ / "dags" / "alertas.py")
alertas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(alertas)

CAMPOS = {
    "dag_id",
    "task_id",
    "run_id",
    "intento",
    "intentos_permitidos",
    "fecha",
    "log_url",
    "tipo_error",
}


def contexto(excepcion=None) -> dict:
    ti = SimpleNamespace(
        dag_id="gemelo_digital_financiero_pipeline",
        task_id="generar_fuentes_sinteticas",
        run_id="demo_20261104_120000",
        try_number=3,
        max_tries=2,
        log_url="http://localhost:8080/dags/x/grid?task_id=y",
    )
    return {"task_instance": ti, "exception": excepcion}


@pytest.fixture
def ruta(tmp_path, monkeypatch):
    destino = tmp_path / "calidad" / "alertas_airflow.jsonl"
    monkeypatch.setenv("ALERTAS_RUTA", str(destino))
    monkeypatch.delenv("ALERTAS_WEBHOOK_URL", raising=False)
    return destino


def test_registro_trae_los_campos_de_la_corrida():
    ahora = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)
    registro = alertas.registro_de_alerta(contexto(ValueError("x")), ahora)
    assert set(registro) == CAMPOS
    assert registro["intento"] == 3
    assert registro["intentos_permitidos"] == 3
    assert registro["fecha"] == "2026-11-04T18:00:00+00:00"
    assert registro["tipo_error"] == "ValueError"


def test_registro_no_copia_el_mensaje_de_error():
    # El mensaje de una excepcion puede traer datos de clientes o secretos;
    # solo se guarda el nombre de su clase.
    sensible = "cliente Maria Lopez, saldo 48,210.55"
    registro = alertas.registro_de_alerta(contexto(RuntimeError(sensible)))
    texto = json.dumps(registro)
    assert "Maria" not in texto
    assert "48,210" not in texto
    assert registro["tipo_error"] == "RuntimeError"


def test_sin_excepcion_el_tipo_queda_vacio():
    assert alertas.registro_de_alerta(contexto())["tipo_error"] is None


def test_callback_agrega_una_linea_por_llamada(ruta):
    alertas.alertar_fallo(contexto(ValueError()))
    alertas.alertar_fallo(contexto(KeyError()))

    lineas = ruta.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 2
    assert [json.loads(linea)["tipo_error"] for linea in lineas] == [
        "ValueError",
        "KeyError",
    ]


def test_webhook_apagado_por_default(ruta, monkeypatch):
    def no_llamar(*_args, **_kwargs):
        raise AssertionError("No debe llamar al webhook sin ALERTAS_WEBHOOK_URL")

    monkeypatch.setattr(alertas, "enviar_webhook", no_llamar)
    alertas.alertar_fallo(contexto())
    assert ruta.exists()


def test_webhook_recibe_la_misma_alerta(ruta, monkeypatch):
    enviados = []
    monkeypatch.setenv("ALERTAS_WEBHOOK_URL", "http://webhook.test/alertas")
    monkeypatch.setattr(
        alertas,
        "enviar_webhook",
        lambda registro, url: enviados.append((url, registro)),
    )
    alertas.alertar_fallo(contexto())

    linea = json.loads(ruta.read_text(encoding="utf-8"))
    assert enviados == [("http://webhook.test/alertas", linea)]


def test_fallo_del_webhook_no_se_propaga(ruta, monkeypatch):
    def caido(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setenv("ALERTAS_WEBHOOK_URL", "http://webhook.test/alertas")
    monkeypatch.setattr(alertas, "enviar_webhook", caido)
    alertas.alertar_fallo(contexto())
    assert len(ruta.read_text(encoding="utf-8").splitlines()) == 1


def test_el_modulo_no_importa_airflow():
    fuente = (RAIZ / "dags" / "alertas.py").read_text(encoding="utf-8")
    assert "import airflow" not in fuente
    assert "from airflow" not in fuente

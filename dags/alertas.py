"""
dags/alertas.py

Alerta por fallo de una tarea del DAG. Airflow llama on_failure_callback
solo cuando la tarea queda en "failed", es decir, despues de agotar sus
reintentos; un intento que se va a reintentar pasa por on_retry_callback,
que no se usa. Asi cada tarea fallida deja exactamente una alerta.

La alerta es una linea JSON en un archivo acumulable,
data/calidad/alertas_airflow.jsonl en el host (montado en el contenedor de
Airflow como /opt/airflow/calidad, ver docker-compose.yml). La pagina de
Observabilidad del dashboard muestra las mas recientes.

Solo lleva identificadores de la corrida y el nombre de la excepcion: nada
del mensaje de error ni de los datos, que podrian traer PII o secretos.

Webhook opcional: si ALERTAS_WEBHOOK_URL esta definida, la misma alerta se
manda por POST como JSON. Apagado por default; un fallo del webhook no
impide escribir la linea ni se propaga a Airflow.

No importa Airflow: recibe el contexto de la tarea como diccionario, asi
que las pruebas lo ejercitan con objetos falsos.
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RUTA_DEFAULT = "/opt/airflow/calidad/alertas_airflow.jsonl"
TIMEOUT_WEBHOOK_SEG = 5


def registro_de_alerta(contexto: dict, ahora: datetime | None = None) -> dict:
    """Arma la alerta a partir del contexto que Airflow pasa al callback."""
    ti = contexto["task_instance"]
    excepcion = contexto.get("exception")
    ahora = ahora or datetime.now(timezone.utc)
    return {
        "dag_id": ti.dag_id,
        "task_id": ti.task_id,
        "run_id": ti.run_id,
        "intento": ti.try_number,
        # max_tries de Airflow es el numero de reintentos, sin el primero.
        "intentos_permitidos": ti.max_tries + 1,
        "fecha": ahora.isoformat(timespec="seconds"),
        "log_url": ti.log_url,
        "tipo_error": type(excepcion).__name__ if excepcion is not None else None,
    }


def escribir_alerta(registro: dict, ruta: str | Path) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def enviar_webhook(registro: dict, url: str) -> None:
    peticion = urllib.request.Request(
        url,
        data=json.dumps(registro, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(peticion, timeout=TIMEOUT_WEBHOOK_SEG):
        pass


def alertar_fallo(contexto: dict) -> None:
    """on_failure_callback del DAG."""
    registro = registro_de_alerta(contexto)
    escribir_alerta(registro, os.environ.get("ALERTAS_RUTA", RUTA_DEFAULT))
    print(f"[Alerta] {json.dumps(registro, ensure_ascii=False)}")

    url = os.environ.get("ALERTAS_WEBHOOK_URL", "").strip()
    if url:
        try:
            enviar_webhook(registro, url)
        except Exception as error:  # noqa: BLE001 - el webhook es opcional
            print(f"[Alerta] No se pudo enviar al webhook: {type(error).__name__}")

"""
dags/gemelo_pipeline_dag.py

DAG que orquesta el pipeline completo: generacion de datos sinteticos
-> ingesta Bronze -> transformacion Silver -> transformacion Gold ->
etiquetas de riesgo -> entrenamiento del modelo -> prediccion de riesgo
crediticio.

Cada tarea lanza el contenedor "worker" (definido en docker-compose.yml)
para hacer el trabajo pesado — Airflow solo decide CUANDO y en que
ORDEN correr cada paso, con reintentos automaticos si algo falla.
Esta separacion es la que permite escalar el computo (worker) sin
tocar el orquestador (Airflow).

Por que el entrenamiento vive dentro del DAG: antes, predict_risk
asumia que models/risk_model.joblib ya existia. En la maquina donde se
desarrollo el modelo existia (y ademas quedaba copiado dentro de la
imagen por el COPY del Dockerfile), pero en un clone limpio la ultima
tarea fallaba. Como los datos se regeneran con semillas fijas en cada
corrida, reentrenar es deterministico y cuesta segundos (500 clientes),
asi que el modelo siempre corresponde a los datos de esa misma corrida.

Nota de portabilidad: DockerOperator lanza contenedores "hermanos" a
traves del socket de Docker del sistema. Las rutas de Mount() se
resuelven desde la maquina anfitriona (el host), no desde dentro del
contenedor de Airflow. Por eso este DAG lee HOST_PROJECT_DIR de una
variable de entorno en vez de usar una ruta fija: cada persona que
corra este proyecto define esa variable una sola vez en su .env
(setup.sh la genera), apuntando a donde clono el repo en su maquina.
"""

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

HOST_PROJECT_DIR = os.environ["HOST_PROJECT_DIR"]
WORKER_IMAGE = "gemelo-worker:local"

default_args = {
    "owner": "andrew",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}

# Carpetas del host que cada tarea necesita ver dentro del worker.
# models/ se monta para que el modelo entrenado por una tarea sea el
# mismo que lee la siguiente, y quede disponible en el host despues.
MOUNTS = [
    Mount(
        source=f"{HOST_PROJECT_DIR}/{carpeta}",
        target=f"/opt/lakehouse/{carpeta}",
        type="bind",
    )
    for carpeta in ("data", "config", "models")
]


def tarea_worker(task_id: str, command: str) -> DockerOperator:
    """Crea una tarea que corre un script del proyecto en el worker.

    Centraliza la configuracion comun (imagen, montajes, red, limpieza)
    para que agregar un paso nuevo al pipeline sea una sola linea.
    """
    return DockerOperator(
        task_id=task_id,
        image=WORKER_IMAGE,
        command=command,
        mounts=MOUNTS,
        network_mode="bridge",
        auto_remove="success",
        docker_url="unix://var/run/docker.sock",
    )


with DAG(
    dag_id="gemelo_digital_financiero_pipeline",
    description=(
        "Genera datos sinteticos, ingiere Bronze, transforma a Silver y Gold, "
        "entrena el modelo de riesgo y predice probabilidad de impago"
    ),
    default_args=default_args,
    schedule=None,  # disparo manual; cambiar a "@daily" cuando el proyecto lo requiera
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["gemelo-digital-financiero", "lakehouse"],
) as dag:

    generar_fuentes = tarea_worker(
        "generar_fuentes_sinteticas",
        "generate_synthetic_sources.py --clientes 500 --meses 12 --out data/raw_sources",
    )

    ingesta_bronze = tarea_worker(
        "ingesta_bronze",
        "ingest_bronze.py --source data/raw_sources --out data/bronze",
    )

    transformacion_silver = tarea_worker(
        "transformacion_silver",
        "transform_silver.py --bronze data/bronze --silver data/silver "
        "--quarantine data/silver_quarantine --rules config/business_rules.yaml",
    )

    transformacion_gold = tarea_worker(
        "transformacion_gold",
        "transform_gold.py --silver data/silver --out data/gold/kpis.duckdb "
        "--catalog config/kpi_catalog.yaml",
    )

    generar_etiquetas = tarea_worker(
        "generar_etiquetas",
        "generate_labels.py --silver data/silver --out data/labels/risk_labels.parquet",
    )

    entrenar_modelo = tarea_worker(
        "entrenar_modelo",
        "train_model.py --silver data/silver --gold data/gold/kpis.duckdb "
        "--labels data/labels/risk_labels.parquet "
        "--model-out models/risk_model.joblib --shap-out models/shap_importancia.png",
    )

    predict_risk = tarea_worker(
        "predict_risk",
        "predict_risk.py --silver data/silver --gold data/gold/kpis.duckdb "
        "--model models/risk_model.joblib",
    )

    (
        generar_fuentes
        >> ingesta_bronze
        >> transformacion_silver
        >> transformacion_gold
        >> generar_etiquetas
        >> entrenar_modelo
        >> predict_risk
    )

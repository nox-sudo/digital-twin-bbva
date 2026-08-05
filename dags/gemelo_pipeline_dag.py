"""
dags/gemelo_pipeline_dag.py

DAG que orquesta el pipeline completo: generacion de datos sinteticos
-> ingesta Bronze -> transformacion Silver.

Cada tarea lanza el contenedor "worker" (definido en docker-compose.yml)
para hacer el trabajo pesado — Airflow solo decide CUANDO y en que
ORDEN correr cada paso, con reintentos automaticos si algo falla.
Esta separacion es la que permite escalar el computo (worker) sin
tocar el orquestador (Airflow).

Nota de portabilidad: DockerOperator lanza contenedores "hermanos" a
traves del socket de Docker del sistema. Las rutas de Mount() se
resuelven desde la maquina anfitriona (el host), no desde dentro del
contenedor de Airflow. Por eso este DAG lee HOST_PROJECT_DIR de una
variable de entorno en vez de usar una ruta fija: cada persona que
corra este proyecto define esa variable una sola vez en su .env,
apuntando a donde clono el repo en su propia maquina. Sin esto, el DAG
funcionaria en una maquina y fallaria en cualquier otra.
"""

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

HOST_PROJECT_DIR = os.environ["HOST_PROJECT_DIR"]

default_args = {
    "owner": "andrew",
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}

with DAG(
    dag_id="gemelo_digital_financiero_pipeline",
    description="Genera datos sinteticos, ingiere Bronze, transforma a Silver",
    default_args=default_args,
    schedule=None,  # disparo manual; cambiar a "@daily" cuando el proyecto lo requiera
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["gemelo-digital-financiero", "lakehouse"],
) as dag:

    mounts = [
        Mount(
            source=f"{HOST_PROJECT_DIR}/data", target="/opt/lakehouse/data", type="bind"
        ),
        Mount(
            source=f"{HOST_PROJECT_DIR}/config",
            target="/opt/lakehouse/config",
            type="bind",
        ),
    ]

    generar_fuentes = DockerOperator(
        task_id="generar_fuentes_sinteticas",
        image="gemelo-worker:local",
        command="generate_synthetic_sources.py --clientes 500 --meses 12 --out data/raw_sources",
        mounts=mounts,
        network_mode="bridge",
        auto_remove="success",
        docker_url="unix://var/run/docker.sock",
    )

    ingesta_bronze = DockerOperator(
        task_id="ingesta_bronze",
        image="gemelo-worker:local",
        command="ingest_bronze.py --source data/raw_sources --out data/bronze",
        mounts=mounts,
        network_mode="bridge",
        auto_remove="success",
        docker_url="unix://var/run/docker.sock",
    )

    transformacion_silver = DockerOperator(
        task_id="transformacion_silver",
        image="gemelo-worker:local",
        command=(
            "transform_silver.py --bronze data/bronze --silver data/silver "
            "--quarantine data/silver_quarantine --rules config/business_rules.yaml"
        ),
        mounts=mounts,
        network_mode="bridge",
        auto_remove="success",
        docker_url="unix://var/run/docker.sock",
    )

    generar_fuentes >> ingesta_bronze >> transformacion_silver

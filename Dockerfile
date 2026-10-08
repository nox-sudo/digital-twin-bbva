# Dockerfile
#
# Imagen del "worker" del Lakehouse: contiene el entorno de computo
# pesado (Java, PySpark, Delta Lake, dependencias del proyecto) y el
# codigo que procesa Bronze -> Silver -> Gold.
#
# Deliberadamente separada de la imagen de Airflow: el orquestador no
# necesita Java ni PySpark instalados, solo necesita saber lanzar este
# contenedor. Si mas adelante el procesamiento requiere mas CPU/memoria,
# este contenedor escala independiente del orquestador.

FROM python:3.11-slim

# Java 21. (python:3.11-slim paso su base a Debian trixie, que ya no
# ofrece el paquete openjdk-17-jre-headless -- solo openjdk-21-*. En tu
# entorno local usas 21 sin problema, la version exacta de Java no
# afecta el resultado del pipeline.)
#
# libgomp1: runtime de OpenMP que XGBoost necesita para cargar su
# libreria nativa (libxgboost.so). python:3.11-slim no lo trae por
# defecto; sin esto, predict_risk.py falla al importar xgboost dentro
# del contenedor con un error de libreria no encontrada.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jre-headless \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# JAVA_HOME resuelto dinamicamente, sin hardcodear arquitectura (amd64
# vs arm64): dos niveles arriba del binario real de java (siguiendo el
# symlink), no de /usr/bin/java. ENV no ejecuta subshells, asi que la
# resolucion ocurre en este RUN y se expone via un symlink fijo.
RUN ln -s "$(dirname "$(dirname "$(readlink -f "$(which java)")")")" /opt/java_home
ENV JAVA_HOME=/opt/java_home
ENV PATH="${JAVA_HOME}/bin:${PATH}"

# uv, mismo gestor de dependencias que usas en local — asi el
# comportamiento de instalacion es identico dentro y fuera de Docker.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /opt/lakehouse

# Copiar solo los archivos de dependencias primero (aprovecha el cache
# de capas de Docker: si el codigo cambia pero no las dependencias, no
# se reinstala nada).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# A partir de aqui, "uv run" usa el entorno tal como quedo, sin volver a
# sincronizar. Sin esto, uv run instala el grupo dev (pytest, flake8,
# black) en cada ejecucion, y como cada tarea del DAG arranca un
# contenedor nuevo, esa descarga desde PyPI se repetia en cada tarea.
ENV UV_NO_SYNC=1

# Descarga los JARs de Delta Lake una sola vez, al construir la imagen.
# configure_spark_with_delta_pip los resuelve desde Maven Central la
# primera vez que se crea una SparkSession; sin este paso, CADA tarea
# del DAG (contenedor nuevo, borrado al terminar) los volvia a bajar.
# Eso hacia cada corrida dependiente de tener salida a Maven: en una red
# corporativa que lo bloquee, la ingesta fallaba. Con el cache de Ivy
# horneado en la imagen, el worker corre sin red hacia Maven.
RUN uv run python -c "\
from delta import configure_spark_with_delta_pip; \
from pyspark.sql import SparkSession; \
configure_spark_with_delta_pip(SparkSession.builder.master('local[1]')).getOrCreate().stop()"

# Ahora si, el resto del codigo.
COPY . .

# Variables de entorno que espera src/common/spark_session.py — dentro
# de Docker, DATA_ROOT apunta al volumen montado, no a una ruta local.
ENV DATA_ROOT=/opt/lakehouse/data
ENV SPARK_MASTER_URL=local[*]

ENTRYPOINT ["uv", "run", "python"]

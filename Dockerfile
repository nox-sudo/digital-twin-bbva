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
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jre-headless \
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

# Ahora si, el resto del codigo.
COPY . .

# Variables de entorno que espera src/common/spark_session.py — dentro
# de Docker, DATA_ROOT apunta al volumen montado, no a una ruta local.
ENV DATA_ROOT=/opt/lakehouse/data
ENV SPARK_MASTER_URL=local[*]

ENTRYPOINT ["uv", "run", "python"]

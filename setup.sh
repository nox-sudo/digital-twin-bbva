#!/usr/bin/env bash
# setup.sh
#
# Genera el archivo .env automaticamente, sin que la persona tenga que
# editar nada a mano. Tiene dos tipos de valores:
#
# De la maquina (se recalculan en cada corrida):
#   - HOST_PROJECT_DIR  ruta absoluta del proyecto
#   - AIRFLOW_UID       UID del usuario actual
#   - DOCKER_GID        GID dueno del socket de Docker (solo importa en Linux)
#
# Secretos (se generan UNA vez y se conservan):
#   - POSTGRES_PASSWORD, AIRFLOW_ADMIN_PASSWORD, AIRFLOW_FERNET_KEY,
#     AIRFLOW_SECRET_KEY, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, PII_HASH_SALT
#   Se conservan porque cambiarlos rompe lo ya creado: Postgres guarda la
#   contrasena con la que se inicializo, y con otra sal los hashes de PII
#   dejarian de coincidir con los de corridas anteriores.
#
# .env no se sube al repo (.gitignore) y queda con permisos 600 (solo el
# usuario dueno puede leerlo).
#
# Tambien crea las carpetas data/ y models/ con el usuario actual como
# dueno: si no existen cuando Docker monta el volumen, Docker las crea
# como root y despues no se pueden borrar sin sudo.
#
# Uso: bash setup.sh   (normalmente lo invoca demo.sh)

set -euo pipefail

# Ruta del script, no del directorio desde donde se invoca: asi funciona
# igual con "bash setup.sh" que con "bash ~/digital-twin-bbva/setup.sh".
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
USER_ID="$(id -u)"

case "$(uname -s)" in
    Linux)
        # En Linux el socket pertenece al grupo "docker", cuyo GID cambia
        # de una maquina a otra. Airflow necesita ese grupo para poder
        # lanzar el worker via DockerOperator.
        DOCKER_GID="$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo 0)"
        ;;
    Darwin)
        # Docker Desktop expone el socket ya accesible dentro de los
        # contenedores; el GID del symlink en el host no aplica.
        DOCKER_GID=0
        ;;
    MINGW* | MSYS* | CYGWIN*)
        echo "Windows detectado (Git Bash). Este proyecto se corre desde WSL2:"
        echo "  1. Instala WSL2 (wsl --install) y activa la integracion WSL en Docker Desktop."
        echo "  2. Abre una terminal de Ubuntu (WSL) y clona el repo dentro de ~/ (no en /mnt/c)."
        echo "  3. Desde esa terminal: bash demo.sh"
        exit 1
        ;;
    *)
        DOCKER_GID=0
        ;;
esac

command -v openssl >/dev/null 2>&1 || {
    echo "Falta openssl (se usa para generar contrasenas aleatorias)." >&2
    exit 1
}

# Valor actual de una variable en .env, o vacio si no existe.
valor_actual() {
    [ -f "${ENV_FILE}" ] || return 0
    grep -E "^$1=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true
}

# Conserva el secreto si ya existe; si no, lo genera con el comando dado.
secreto() {
    local nombre="$1" generador="$2" actual
    actual="$(valor_actual "${nombre}")"
    if [ -n "${actual}" ]; then
        printf '%s' "${actual}"
    else
        eval "${generador}"
    fi
}

# Hex: seguro dentro de URLs (la contrasena de Postgres va dentro de la
# cadena de conexion de Airflow) y en cualquier shell.
hex() { openssl rand -hex "$1"; }

# Migracion desde la version sin secretos: si hay un .env viejo (sin
# POSTGRES_PASSWORD) y ya existe la base de Airflow, esa base se creo con
# la contrasena fija anterior y no aceptara la nueva.
if [ -f "${ENV_FILE}" ] && [ -z "$(valor_actual POSTGRES_PASSWORD)" ] \
    && docker volume ls -q 2>/dev/null | grep -q 'postgres_data$'; then
    echo "Aviso: se detecto una base de Airflow creada con la configuracion anterior."
    echo "       Para que tome las contrasenas nuevas, borrala una vez (tus datos en data/ no se tocan):"
    echo "         docker compose down -v"
fi

POSTGRES_PASSWORD="$(secreto POSTGRES_PASSWORD 'hex 24')"
AIRFLOW_ADMIN_PASSWORD="$(secreto AIRFLOW_ADMIN_PASSWORD 'hex 12')"
# Fernet exige exactamente 32 bytes en base64 url-safe.
AIRFLOW_FERNET_KEY="$(secreto AIRFLOW_FERNET_KEY "openssl rand -base64 32 | tr '+/' '-_'")"
AIRFLOW_SECRET_KEY="$(secreto AIRFLOW_SECRET_KEY 'hex 32')"
MINIO_ROOT_USER="$(secreto MINIO_ROOT_USER 'printf gemelo-admin')"
MINIO_ROOT_PASSWORD="$(secreto MINIO_ROOT_PASSWORD 'hex 24')"
PII_HASH_SALT="$(secreto PII_HASH_SALT 'hex 32')"

mkdir -p "${PROJECT_DIR}/data" "${PROJECT_DIR}/models"

# umask 077: el archivo nace con permisos 600, sin una ventana en la que
# otro usuario de la maquina pudiera leerlo.
(
    umask 077
    cat > "${ENV_FILE}" << EOF
# Generado por setup.sh. NO subir al repo. Ver .env.example.

# --- De esta maquina (se recalculan en cada corrida de setup.sh)
AIRFLOW_UID=${USER_ID}
HOST_PROJECT_DIR=${PROJECT_DIR}
DOCKER_GID=${DOCKER_GID}

# --- Secretos (generados una vez; no cambiarlos a mano, ver docs/seguridad.md)
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
AIRFLOW_ADMIN_PASSWORD=${AIRFLOW_ADMIN_PASSWORD}
AIRFLOW_FERNET_KEY=${AIRFLOW_FERNET_KEY}
AIRFLOW_SECRET_KEY=${AIRFLOW_SECRET_KEY}
MINIO_ROOT_USER=${MINIO_ROOT_USER}
MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD}
PII_HASH_SALT=${PII_HASH_SALT}
EOF
)
chmod 600 "${ENV_FILE}"

echo ".env listo (permisos 600). Variables de esta maquina:"
grep -E '^(AIRFLOW_UID|HOST_PROJECT_DIR|DOCKER_GID)=' "${ENV_FILE}"
echo "Secretos: generados una vez y guardados en .env (no se imprimen)."

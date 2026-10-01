#!/usr/bin/env bash
# setup.sh
#
# Genera el archivo .env automaticamente, sin que la persona tenga que
# editar nada a mano. Detecta:
#   - la ruta absoluta del proyecto (HOST_PROJECT_DIR)
#   - el UID del usuario actual (AIRFLOW_UID)
#   - el GID dueno del socket de Docker (DOCKER_GID), solo en Linux
#
# Tambien crea las carpetas data/ y models/ con el usuario actual como
# dueno: si no existen cuando Docker monta el volumen, Docker las crea
# como root y despues no se pueden borrar sin sudo.
#
# Uso: bash setup.sh   (normalmente lo invoca demo.sh, no hace falta
# correrlo a mano)

set -euo pipefail

# Ruta del script, no del directorio desde donde se invoca: asi funciona
# igual con "bash setup.sh" que con "bash ~/digital-twin-bbva/setup.sh".
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

mkdir -p "${PROJECT_DIR}/data" "${PROJECT_DIR}/models"

cat > "${PROJECT_DIR}/.env" << EOF
AIRFLOW_UID=${USER_ID}
HOST_PROJECT_DIR=${PROJECT_DIR}
DOCKER_GID=${DOCKER_GID}
EOF

echo "Archivo .env generado:"
cat "${PROJECT_DIR}/.env"

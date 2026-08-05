#!/bin/bash
# setup.sh
#
# Genera el archivo .env automaticamente, sin que la persona tenga que
# editar nada a mano. Detecta la ruta absoluta del proyecto y el UID
# del usuario actual.
#
# Uso: bash setup.sh

set -e

PROJECT_DIR="$(pwd)"
USER_ID="$(id -u)"

cat > .env << EOF
AIRFLOW_UID=${USER_ID}
HOST_PROJECT_DIR=${PROJECT_DIR}
EOF

echo "Archivo .env generado:"
cat .env
echo ""
echo "Listo. Ahora corre: docker compose up --build -d"

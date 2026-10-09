#!/usr/bin/env bash
# demo.sh
#
# Punto de entrada unico para correr el Gemelo Digital Financiero en
# cualquier maquina con Docker. Pensado para que alguien que nunca ha
# visto el repo (mentor, evaluador) lo levante con un solo comando, sin
# conocer Airflow ni la estructura del proyecto.
#
# Uso:
#   bash demo.sh              levanta todo y corre el pipeline completo (= levantar)
#   bash demo.sh levantar     igual que arriba
#   bash demo.sh pipeline     corre el pipeline directo en el worker, sin Airflow
#                             (ruta rapida: no levanta Airflow, Postgres ni MinIO)
#   bash demo.sh reporte      genera data/reporte_pipeline.html con conteos y tiempos
#   bash demo.sh estado       muestra el estado de las ultimas corridas del DAG
#   bash demo.sh bajar        detiene los contenedores (conserva datos)
#   bash demo.sh nueva-entrega  simula que llega el siguiente mes de datos y lo procesa
#                             (requiere haber corrido levantar antes)
#   bash demo.sh calidad      reporte de calidad: rechazos por regla, nulos y tendencia
#   bash demo.sh sesiones     lista las sesiones de datos guardadas en data/sesiones/
#   bash demo.sh limpiar      borra capas, modelo y volumenes; conserva las sesiones
#   bash demo.sh limpiar todo igual, pero borra tambien las sesiones
#   bash demo.sh respaldar [destino]
#                             copia sesiones, landing zone (MinIO) e historico de
#                             calidad fuera del repo, con manifiesto SHA-256
#   bash demo.sh restaurar <respaldo>
#                             verifica el respaldo y lo restaura en data/; luego
#                             bash demo.sh reconstruye Bronze/Silver/Gold desde ahi
#
# Guia completa: docs/ejecucion-local.md

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_DIR}"

DAG_ID="gemelo_digital_financiero_pipeline"
AIRFLOW_URL="http://localhost:8080"
MEMORIA_MINIMA_GB=6
TIMEOUT_AIRFLOW_SEG=600   # el primer arranque instala el provider de Docker
TIMEOUT_PIPELINE_SEG=1800
MAX_FALLOS_SONDEO=3      # consultas seguidas a Airflow que pueden fallar antes de abortar
# Lo que no se puede regenerar y por eso forma parte del respaldo.
# Bronze, Silver, Gold y el modelo se reconstruyen desde la landing zone
# (data/minio) con "bash demo.sh". data/calidad se incluye porque guarda el
# historico entre corridas (tendencia de incidencias) y no se recalcula.
DIRS_RESPALDO=(sesiones minio calidad)

info() { printf '\n==> %s\n' "$*"; }
error() {
    printf '\nERROR: %s\n' "$*" >&2
    exit 1
}

# airflow CLI dentro del scheduler. stderr se descarta porque Airflow
# imprime advertencias de configuracion en cada llamada.
airflow_cli() {
    docker compose exec -T airflow-scheduler airflow "$@" 2>/dev/null
}

# Para comandos que cambian estado (unpause, trigger): si fallan, muestra
# el error de Airflow en vez de dejar que "set -e" termine el script sin
# explicacion. Se siguen filtrando las advertencias de configuracion.
airflow_cli_o_error() {
    local salida
    if ! salida="$(docker compose exec -T airflow-scheduler airflow "$@" 2>&1)"; then
        printf '%s\n' "${salida}" | grep -v FutureWarning | tail -n 15 >&2 || true
        error "Fallo el comando: airflow $*"
    fi
}

verificar_requisitos() {
    command -v docker >/dev/null 2>&1 \
        || error "Docker no esta instalado. Instala Docker Desktop (Mac/Windows) o Docker Engine (Linux)."
    docker info >/dev/null 2>&1 \
        || error "Docker esta instalado pero no esta corriendo. Abre Docker Desktop y vuelve a intentar."
    docker compose version >/dev/null 2>&1 \
        || error "Falta Docker Compose v2 ('docker compose', sin guion)."

    local memoria_bytes memoria_gb
    memoria_bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
    memoria_gb=$((memoria_bytes / 1024 / 1024 / 1024))
    if [ "${memoria_gb}" -lt "${MEMORIA_MINIMA_GB}" ]; then
        echo "Aviso: Docker tiene ${memoria_gb} GB de memoria asignada; se recomiendan ${MEMORIA_MINIMA_GB} GB."
        echo "       En Docker Desktop: Settings > Resources > Memory."
    fi
}

preparar_entorno() {
    # Siempre, no solo si falta .env: setup.sh conserva los secretos que
    # ya existen y agrega los que falten. Un .env de una version anterior
    # (sin secretos) haria que docker compose se negara a arrancar.
    bash setup.sh >/dev/null
}

esperar_airflow() {
    info "Esperando a que Airflow este listo (el primer arranque tarda unos minutos)"
    local inicio=$SECONDS
    until curl -fsS "${AIRFLOW_URL}/health" 2>/dev/null | grep -Eq '"scheduler": *\{[^}]*"healthy"'; do
        [ $((SECONDS - inicio)) -gt ${TIMEOUT_AIRFLOW_SEG} ] \
            && error "Airflow no respondio en ${TIMEOUT_AIRFLOW_SEG}s. Revisa: docker compose logs airflow-scheduler"
        sleep 5
    done

    # El scheduler puede estar sano antes de haber leido el archivo del
    # DAG; unpause/trigger fallan si el DAG aun no esta registrado.
    until airflow_cli dags list -o plain | grep -q "${DAG_ID}"; do
        [ $((SECONDS - inicio)) -gt ${TIMEOUT_AIRFLOW_SEG} ] \
            && error "El DAG no aparecio en Airflow. Revisa: docker compose logs airflow-scheduler"
        sleep 5
    done
    echo "Airflow listo."
}

# Estado del run ("queued", "running", "success", "failed") o vacio si
# Airflow aun no lo lista. Devuelve 1 si la consulta misma fallo, para que
# correr_dag decida cuantos fallos seguidos tolera: dentro de una
# sustitucion de comando, un fallo del pipeline mataria el script (set -e
# con pipefail) sin ningun mensaje. Se usa una here-string en vez de una
# tuberia para evitar tambien el SIGPIPE.
estado_del_run() {
    local salida
    salida="$(airflow_cli dags list-runs -d "${DAG_ID}" -o plain)" || return 1
    awk -v id="$1" '$2 == id {print $3}' <<< "${salida}"
}

# Primera tarea en estado "running" del run, o vacio si no hay ninguna.
# Mismo contrato de retorno que estado_del_run.
tarea_en_curso() {
    local salida
    salida="$(airflow_cli tasks states-for-dag-run "${DAG_ID}" "$1" -o plain)" || return 1
    awk '$4 == "running" {print $3; exit}' <<< "${salida}"
}

correr_dag() {
    # No usar "${1:-{\}}": en el bash 3.2 de macOS conserva la barra y
    # produce "{\}", que no es JSON valido y hace fallar el trigger.
    local conf="${1:-}"
    [ -n "${conf}" ] || conf='{}'
    local run_id
    run_id="demo_$(date +%Y%m%d_%H%M%S)"

    info "Disparando el DAG ${DAG_ID} (run_id: ${run_id})"
    # El DAG nace pausado por default; sin unpause, el trigger queda en cola.
    airflow_cli_o_error dags unpause "${DAG_ID}"
    airflow_cli_o_error dags trigger "${DAG_ID}" --run-id "${run_id}" --conf "${conf}"

    local inicio=$SECONDS estado="queued" ultima_linea="" fallos_seguidos=0
    while [ "${estado}" != "success" ] && [ "${estado}" != "failed" ]; do
        [ $((SECONDS - inicio)) -gt ${TIMEOUT_PIPELINE_SEG} ] \
            && error "El pipeline sigue corriendo tras ${TIMEOUT_PIPELINE_SEG}s. Revisa la UI: ${AIRFLOW_URL}"
        sleep 10
        local nuevo_estado en_curso
        # Un fallo puntual de "docker compose exec" no debe tumbar una
        # corrida que sigue avanzando en Airflow: se reintenta, y solo se
        # aborta (con mensaje) si la consulta falla varias veces seguidas.
        if nuevo_estado="$(estado_del_run "${run_id}")" \
            && en_curso="$(tarea_en_curso "${run_id}")"; then
            fallos_seguidos=0
            estado="${nuevo_estado}"
        else
            fallos_seguidos=$((fallos_seguidos + 1))
            if [ "${fallos_seguidos}" -ge "${MAX_FALLOS_SONDEO}" ]; then
                error "No se pudo consultar el estado del DAG (${fallos_seguidos} intentos seguidos). El run ${run_id} puede seguir corriendo en Airflow. Revisa: docker compose ps, docker compose logs airflow-scheduler, ${AIRFLOW_URL}"
            fi
            continue
        fi
        local linea="estado: ${estado:-queued}${en_curso:+ | tarea en curso: ${en_curso}}"
        if [ "${linea}" != "${ultima_linea}" ]; then
            printf '  [%4ss] %s\n' $((SECONDS - inicio)) "${linea}"
            ultima_linea="${linea}"
        fi
    done

    if [ "${estado}" = "failed" ]; then
        # El detalle por tarea es informativo: si no se puede obtener, el
        # mensaje de error de abajo debe salir igual.
        airflow_cli tasks states-for-dag-run "${DAG_ID}" "${run_id}" -o plain \
            | awk 'NR > 1 {printf "  %-28s %s\n", $3, $4}' || true
        error "El pipeline fallo. Detalle por tarea en ${AIRFLOW_URL} (DAG ${DAG_ID})."
    fi
}

# Lee un valor de .env sin cargar el archivo completo al entorno.
valor_env() {
    grep -E "^$1=" .env | tail -1 | cut -d= -f2-
}

resumen_final() {
    # Las credenciales se generaron para esta maquina (setup.sh) y solo
    # se muestran en esta terminal; no estan en el repo.
    cat << EOF

Pipeline completado.

  Airflow (DAG y logs por tarea)   ${AIRFLOW_URL}        usuario admin / $(valor_env AIRFLOW_ADMIN_PASSWORD)
  MinIO (consola)                  http://localhost:9001 usuario $(valor_env MINIO_ROOT_USER) / $(valor_env MINIO_ROOT_PASSWORD)
  KPIs en Gold (DuckDB)            data/gold/kpis.duckdb
  Modelo de riesgo y grafico SHAP  models/

Siguiente paso sugerido: bash demo.sh reporte
EOF
}

cmd_levantar() {
    verificar_requisitos
    preparar_entorno
    info "Construyendo imagenes y levantando servicios"
    docker compose up --build -d
    esperar_airflow
    correr_dag
    resumen_final
}

cmd_pipeline() {
    verificar_requisitos
    preparar_entorno
    info "Construyendo la imagen del worker"
    docker compose build worker

    # Mismos pasos y argumentos que el DAG, en el mismo orden, via el CLI
    # unico (main.py). Util para una demo rapida o para aislar si un
    # problema es del pipeline o de la orquestacion.
    local pasos=(
        "generate --config config/sesion.yaml --out data/raw_sources"
        "bronze --source data/raw_sources --out data/bronze"
        "silver --bronze data/bronze --silver data/silver --quarantine data/silver_quarantine --rules config/business_rules.yaml"
        "gold --silver data/silver --out data/gold/kpis.duckdb --catalog config/kpi_catalog.yaml"
        "features --silver data/silver --gold data/gold/kpis.duckdb"
        "labels --gold data/gold/kpis.duckdb --config config/etiquetas_impago.yaml --auditoria-out data/auditoria/etiquetas_latentes.parquet"
        "train-model --gold data/gold/kpis.duckdb --model-out models/risk_model.joblib --shap-out models/shap_importancia.png"
        "predict-risk --gold data/gold/kpis.duckdb --model models/risk_model.joblib"
    )
    local paso
    for paso in "${pasos[@]}"; do
        info "Paso: ${paso%% *}"
        # shellcheck disable=SC2086  # se separan argumentos a proposito
        docker compose run --rm worker main.py ${paso}
    done
    resumen_final
}

cmd_reporte() {
    preparar_entorno
    info "Generando reporte del pipeline"
    docker compose run --rm worker pipeline_summary.py --no-abrir --out data/reporte_pipeline.html
    echo "Reporte: ${PROJECT_DIR}/data/reporte_pipeline.html"
}

cmd_estado() {
    airflow_cli dags list-runs -d "${DAG_ID}" -o table \
        || error "Airflow no esta corriendo. Usa: bash demo.sh levantar"
}

cmd_bajar() {
    docker compose down
}

cmd_nueva_entrega() {
    verificar_requisitos
    [ -f data/raw_sources/manifest.json ] \
        || error "No hay una sesion activa todavia. Corre primero: bash demo.sh"
    # Meses adicionales de la sesion activa, leidos del manifest sin
    # depender de Python ni jq en la maquina.
    local actual siguiente
    actual="$(grep -o '"meses_adicionales": *[0-9]*' data/raw_sources/manifest.json \
        | grep -o '[0-9]*$' || echo 0)"
    siguiente=$((${actual:-0} + 1))
    info "Nueva entrega: mes adicional ${siguiente} sobre la sesion base"
    esperar_airflow
    correr_dag "{\"meses_adicionales\": ${siguiente}}"
    echo "La entrega trae solo el mes nuevo; Bronze lo agrega a lo que ya tenia."
}

cmd_calidad() {
    preparar_entorno
    docker compose run --rm worker quality_report.py
}

cmd_sesiones() {
    if [ ! -d data/sesiones ] || [ -z "$(ls -A data/sesiones 2>/dev/null)" ]; then
        echo "No hay sesiones guardadas todavia."
        return
    fi
    echo "Sesiones en data/sesiones/ (semilla_fecha_clientes_meses):"
    find data/sesiones -mindepth 1 -maxdepth 1 -type d ! -name '.*' -exec basename {} \; \
        | sort | sed 's/^/  /'
    echo "Parametros de la sesion por default: config/sesion.yaml"
}

# SHA-256 de cada archivo bajo los directorios dados (rutas relativas a $1),
# ordenado por ruta. shasum viene con macOS; sha256sum con Linux.
manifiesto_de() {
    local base="$1"
    shift
    local hash=(shasum -a 256)
    command -v shasum >/dev/null 2>&1 || hash=(sha256sum)
    (cd "${base}" && find "$@" -type f -print0 | LC_ALL=C sort -z | xargs -0 "${hash[@]}")
}

# Verdadero si hay un Docker usable y estamos en un proyecto con compose.
compose_disponible() {
    [ -f docker-compose.yml ] && command -v docker >/dev/null 2>&1 \
        && docker info >/dev/null 2>&1
}

minio_corriendo() {
    compose_disponible \
        && [ -n "$(docker compose ps --status running -q minio 2>/dev/null)" ]
}

# MinIO escribe en data/minio mientras corre: copiarlo en caliente puede
# dar una landing zone inconsistente. respaldar lo detiene y lo reinicia
# al terminar, incluso si la copia falla (trap EXIT).
MINIO_DETENIDO=0
reiniciar_minio() {
    if [ "${MINIO_DETENIDO}" = 1 ]; then
        MINIO_DETENIDO=0
        docker compose start minio >/dev/null 2>&1 || true
    fi
}

# Subconjunto de DIRS_RESPALDO que existe en data/ (o en $1 si se indica).
dirs_presentes() {
    local base="${1:-data}" d
    for d in "${DIRS_RESPALDO[@]}"; do
        if [ -d "${base}/${d}" ]; then
            echo "${d}"
        fi
    done
}

cmd_respaldar() {
    local destino="${1:-${HOME}/respaldos-gemelo/$(date +%Y-%m-%d_%H%M%S)}"
    local presentes=() d
    while IFS= read -r d; do
        [ -n "${d}" ] && presentes+=("${d}")
    done < <(dirs_presentes data)
    [ "${#presentes[@]}" -gt 0 ] \
        || error "No hay nada que respaldar: no existe ninguno de data/{${DIRS_RESPALDO[*]}}."
    [ -n "$(cd data && find "${presentes[@]}" -type f | head -n 1)" ] \
        || error "Los directorios a respaldar no tienen archivos (${presentes[*]})."

    mkdir -p "$(dirname "${destino}")"
    destino="$(cd "$(dirname "${destino}")" && pwd)/$(basename "${destino}")"
    case "${destino}/" in
        "${PROJECT_DIR}/"*)
            error "El destino no debe estar dentro del repo (${PROJECT_DIR}): un respaldo ahi se versionaria o lo borraria limpiar." ;;
    esac
    [ ! -e "${destino}" ] \
        || error "${destino} ya existe; elige otro destino para no mezclar respaldos."

    trap reiniciar_minio EXIT
    if minio_corriendo; then
        info "Deteniendo MinIO mientras se copia (para que la landing zone quede consistente)"
        docker compose stop minio >/dev/null
        MINIO_DETENIDO=1
    fi

    info "Copiando ${presentes[*]} a ${destino}"
    local manifiesto_origen manifiesto_copia
    manifiesto_origen="$(manifiesto_de data "${presentes[@]}")"
    mkdir -p "${destino}"
    chmod 700 "${destino}"
    for d in "${presentes[@]}"; do
        cp -Rp "data/${d}" "${destino}/${d}"
    done
    reiniciar_minio

    manifiesto_copia="$(manifiesto_de "${destino}" "${presentes[@]}")"
    [ "${manifiesto_origen}" = "${manifiesto_copia}" ] \
        || error "La copia no coincide con el original (SHA-256). Revisa ${destino}."
    printf '%s\n' "${manifiesto_origen}" > "${destino}/MANIFIESTO.sha256"

    echo "Respaldo listo: $(printf '%s\n' "${manifiesto_origen}" | wc -l | tr -d ' ') archivos, $(du -sh "${destino}" | cut -f1 | tr -d ' '), en ${destino}"
    echo "Verificado contra el original con SHA-256 (MANIFIESTO.sha256)."
    echo "No incluye .env. Guarda aparte PII_HASH_SALT: con otra sal, los hashes de PII"
    echo "reconstruidos no coinciden con los de antes."
}

cmd_restaurar() {
    local origen="${1:-}"
    [ -n "${origen}" ] || error "Uso: bash demo.sh restaurar <carpeta-del-respaldo>"
    [ -f "${origen}/MANIFIESTO.sha256" ] \
        || error "${origen} no parece un respaldo de demo.sh (falta MANIFIESTO.sha256)."
    origen="$(cd "${origen}" && pwd)"

    local presentes=() d
    while IFS= read -r d; do
        [ -n "${d}" ] && presentes+=("${d}")
    done < <(dirs_presentes "${origen}")
    [ "${#presentes[@]}" -gt 0 ] \
        || error "El respaldo no contiene ninguno de ${DIRS_RESPALDO[*]}."

    info "Verificando la integridad del respaldo"
    [ "$(manifiesto_de "${origen}" "${presentes[@]}")" = "$(cat "${origen}/MANIFIESTO.sha256")" ] \
        || error "El respaldo no coincide con su manifiesto (archivos alterados, faltantes o de mas). No se restaura nada."

    for d in "${presentes[@]}"; do
        if [ -n "$(ls -A "data/${d}" 2>/dev/null)" ]; then
            error "data/${d} ya tiene contenido; restaurar no sobrescribe. Corre antes: bash demo.sh limpiar todo"
        fi
    done

    if [ -f .env ] && compose_disponible; then
        info "Deteniendo el stack (restaurar no se hace con MinIO escribiendo)"
        docker compose down >/dev/null 2>&1 || true
    fi

    info "Restaurando ${presentes[*]} en data/"
    mkdir -p data
    for d in "${presentes[@]}"; do
        # Un directorio vacio hace que cp copie ADENTRO en vez de reemplazarlo.
        [ -d "data/${d}" ] && rmdir "data/${d}"
        cp -Rp "${origen}/${d}" "data/${d}"
    done
    [ "$(manifiesto_de data "${presentes[@]}")" = "$(cat "${origen}/MANIFIESTO.sha256")" ] \
        || error "Lo restaurado no coincide con el manifiesto. Revisa data/."

    echo "Restaurado y verificado con SHA-256."
    echo "Siguiente paso: bash demo.sh  (reconstruye Bronze, Silver, Gold y el modelo desde la landing zone)"
    if [ ! -f .env ]; then
        echo "Aviso: no hay .env. demo.sh generara uno NUEVO con otra PII_HASH_SALT; para que los"
        echo "hashes de PII coincidan con los de antes, copia tu .env original antes de correr demo.sh."
    fi
}

cmd_limpiar() {
    local alcance="${1:-}" que_se_borra="capas Bronze/Silver/Gold, modelo y volumenes (se conservan las sesiones y el historico de MinIO)"
    local filtro="! -name sesiones ! -name minio"
    if [ "${alcance}" = "todo" ]; then
        que_se_borra="todo data/ INCLUIDAS las sesiones y el historico de MinIO, el modelo y los volumenes"
        filtro=""
    fi
    printf 'Esto borra %s. Escribe "si" para continuar: ' "${que_se_borra}"
    local respuesta
    read -r respuesta
    [ "${respuesta}" = "si" ] || error "Cancelado."
    preparar_entorno
    # Los archivos los escribio el worker (root dentro del contenedor);
    # se borran desde un contenedor para no necesitar sudo en Linux.
    docker compose run --rm --entrypoint sh worker -c \
        "find /opt/lakehouse/data -mindepth 1 -maxdepth 1 ${filtro} -exec rm -rf {} + ;
         rm -f /opt/lakehouse/models/*.joblib /opt/lakehouse/models/*.png"
    docker compose down -v
    echo "Listo."
}

case "${1:-levantar}" in
    levantar) cmd_levantar ;;
    pipeline) cmd_pipeline ;;
    reporte) cmd_reporte ;;
    estado) cmd_estado ;;
    bajar) cmd_bajar ;;
    nueva-entrega) cmd_nueva_entrega ;;
    calidad) cmd_calidad ;;
    sesiones) cmd_sesiones ;;
    limpiar) cmd_limpiar "${2:-}" ;;
    respaldar) cmd_respaldar "${2:-}" ;;
    restaurar) cmd_restaurar "${2:-}" ;;
    -h | --help | ayuda) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) error "Comando desconocido: $1 (usa: bash demo.sh ayuda)" ;;
esac

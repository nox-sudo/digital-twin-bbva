# Ejecucion local

Guia para correr el pipeline completo en una maquina nueva, sin conocer
el proyecto de antemano. Un comando levanta la infraestructura, corre
el DAG de punta a punta y deja los resultados en `data/` y `models/`.

## Requisitos

| Requisito | Detalle |
|---|---|
| Docker | Docker Desktop (Mac/Windows) o Docker Engine + Compose v2 (Linux) |
| Memoria asignada a Docker | 6 GB recomendados (Spark + Airflow) |
| Disco libre | ~6 GB (imagenes de Airflow, Postgres, MinIO y worker) |
| Puertos libres | 8080 (Airflow), 9000 y 9001 (MinIO) |
| Red | Docker Hub, PyPI y Maven Central, solo durante el primer build |
| Windows | Solo via WSL2 (ver abajo) |

No se necesita Python, Java ni Spark instalados en la maquina: todo
corre dentro de contenedores.

## Inicio rapido

```bash
git clone https://github.com/nox-sudo/digital-twin-bbva.git
cd digital-twin-bbva
git checkout develop
bash demo.sh
```

`demo.sh` hace, en orden:

1. Verifica que Docker este instalado, corriendo, y con memoria suficiente.
2. Genera `.env` con los valores de esta maquina (`setup.sh`): ruta del
   proyecto, UID del usuario y, en Linux, el grupo del socket de Docker.
3. Construye la imagen del worker y levanta Postgres, Airflow y MinIO.
4. Espera a que Airflow este sano y haya registrado el DAG.
5. Despausa y dispara `gemelo_digital_financiero_pipeline`, y muestra el
   avance tarea por tarea hasta que termina.

El primer arranque tarda mas (descarga de imagenes y build del worker);
los siguientes reutilizan el cache de Docker.

## Que revisar al terminar

| Que | Donde |
|---|---|
| DAG, grafo de tareas y logs de cada una | http://localhost:8080 (usuario `admin`; la contrasena la muestra `demo.sh` al terminar, y esta en `.env`) |
| KPIs de Gold (12 KPIs, formato largo) | `data/gold/kpis.duckdb`, tabla `gold_kpis` |
| Features por cliente (una fila por cliente) | `data/gold/kpis.duckdb`, tabla `gold_features_cliente` |
| Modelo de riesgo y grafico SHAP | `models/risk_model.joblib`, `models/shap_importancia.png` |
| Filas rechazadas por validaciones de Silver | `data/silver_quarantine/` (vacio con datos limpios) |
| Logs de cada corrida | `data/logs/` |
| Sesion de datos usada, con parametros y checksums | `data/sesiones/<id>/manifest.json` |
| Resumen visual Bronze/Silver | `bash demo.sh reporte` genera `data/reporte_pipeline.html` |

Consulta rapida a Gold sin instalar nada:

```bash
docker compose run --rm worker -c "
import duckdb
con = duckdb.connect('data/gold/kpis.duckdb', read_only=True)
print(con.sql('SELECT kpi_id, COUNT(*) AS clientes FROM gold_kpis GROUP BY 1 ORDER BY 1'))
"
```

## Comandos

| Comando | Uso |
|---|---|
| `bash demo.sh` | Levanta todo y corre el pipeline (igual a `levantar`) |
| `bash demo.sh pipeline` | Corre los 8 pasos directo en el worker, sin Airflow. Mas rapido; util si solo interesa ver el procesamiento de datos |
| `bash demo.sh reporte` | Genera el reporte HTML de conteos y tiempos |
| `bash demo.sh estado` | Lista las corridas del DAG |
| `bash demo.sh bajar` | Detiene los contenedores, conserva datos |
| `bash demo.sh nueva-entrega` | Simula que llega el siguiente mes: genera la entrega, la publica en MinIO y Bronze ingiere solo lo nuevo |
| `bash demo.sh calidad` | Reporte de calidad: filas rechazadas por regla y columna, nulos por columna y tendencia entre corridas |
| `bash demo.sh sesiones` | Lista las sesiones de datos guardadas |
| `bash demo.sh limpiar` | Borra capas, modelo y volumenes; conserva las sesiones (pide confirmacion) |
| `bash demo.sh limpiar todo` | Igual, pero borra tambien las sesiones |
| `bash demo.sh respaldar [destino]` | Copia sesiones, landing zone (MinIO) e historico de calidad fuera del repo, con manifiesto SHA-256 (ver [Respaldo y restauracion](#respaldo-y-restauracion)) |
| `bash demo.sh restaurar <respaldo>` | Verifica el respaldo y lo restaura en `data/`; despues `bash demo.sh` reconstruye todo lo demas |

## Respaldo y restauracion

Bronze, Silver, Gold y el modelo se pueden reconstruir; lo que no se
puede regenerar es lo siguiente, y por eso es lo unico que se respalda:

| Carpeta | Que guarda |
|---|---|
| `data/sesiones/` | Las sesiones de datos, con manifest y checksums |
| `data/minio/` | La landing zone: el historico inmutable de entregas |
| `data/calidad/` | El historico de calidad entre corridas (la tendencia no se recalcula) |

```bash
bash demo.sh respaldar                       # a ~/respaldos-gemelo/<fecha_hora>/
bash demo.sh respaldar /ruta/de/mi/respaldo  # o a un destino a eleccion
```

El destino debe estar fuera del repo y no existir todavia. Si MinIO esta
corriendo, `respaldar` lo detiene mientras copia (una landing zone copiada en
caliente puede quedar inconsistente) y lo reinicia al terminar. Al final
compara la copia contra el original con SHA-256 y deja `MANIFIESTO.sha256`.

Para recuperar una instancia a partir de un respaldo:

```bash
bash demo.sh limpiar todo             # solo si data/ ya tiene contenido
bash demo.sh restaurar /ruta/del/respaldo
bash demo.sh                          # Bronze se reconstruye desde la landing zone
```

`restaurar` verifica el respaldo contra su manifiesto antes de tocar nada y
se niega a restaurar uno alterado o a sobrescribir datos existentes.

**El `.env` no se respalda**, porque contiene secretos. Guarda aparte al menos
`PII_HASH_SALT`: con otra sal, los hashes de PII que se reconstruyan no
coinciden con los de las corridas anteriores. Si restauras en una maquina sin
`.env`, copia primero el original; si no, `demo.sh` generara uno nuevo.

Lo que **no** se restaura, por ser derivado: las capas Bronze, Silver y Gold,
el modelo y el historial de corridas y logs de Airflow.

## Sesiones de datos

Los datos sinteticos se generan a partir de `config/sesion.yaml`
(semilla, fecha de referencia, clientes, meses). Con los mismos valores
se obtienen exactamente los mismos datos, en cualquier maquina y
cualquier dia. La primera corrida guarda la sesion en
`data/sesiones/<id>/`; las siguientes la reutilizan en segundos.

Para correr con otro volumen, cambiar `clientes` en
`config/sesion.yaml` y volver a correr `bash demo.sh`: se genera una
sesion nueva y la anterior queda guardada.

## Windows

El proyecto monta rutas del host en contenedores lanzados por Airflow
(ver nota de portabilidad en `dags/gemelo_pipeline_dag.py`), y esas
rutas deben tener formato Linux. Por eso en Windows se corre desde WSL2:

1. `wsl --install` en PowerShell como administrador, y reiniciar.
2. En Docker Desktop: Settings > Resources > WSL Integration, activar la
   distribucion (Ubuntu).
3. Abrir la terminal de Ubuntu, clonar el repo dentro de `~/` (no en
   `/mnt/c/...`, que es lento y rompe permisos) y seguir el inicio rapido.

## Problemas comunes

| Sintoma | Causa probable | Solucion |
|---|---|---|
| `Bind for 0.0.0.0:8080 failed: port is already allocated` | Otro servicio usa el puerto | Detenerlo, o cambiar `"8080:8080"` a `"8081:8080"` en `docker-compose.yml` |
| Tareas fallan con `permission denied` sobre `docker.sock` (Linux) | `.env` generado antes de agregar el usuario al grupo `docker` | `rm .env && bash demo.sh` |
| Una tarea Spark muere sin error claro (codigo 137) | Docker sin memoria suficiente | Subir memoria en Docker Desktop a 6 GB o mas |
| El build del worker falla al descargar Delta Lake | Sin acceso a Maven Central durante el build | Construir desde una red con acceso; despues el worker ya no lo necesita |
| Se movio la carpeta del repo y el DAG falla al montar rutas | `.env` apunta a la ruta anterior | `rm .env && bash demo.sh` |

Para detalle de cualquier fallo: pestaña Logs de la tarea en la UI de
Airflow, o `docker compose logs airflow-scheduler`.

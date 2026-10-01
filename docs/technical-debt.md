# Deuda tecnica conocida

Registro de gaps conocidos que no bloquean el estado actual del pipeline,
pero que un futuro colaborador (o evaluador) deberia poder encontrar aca,
sin tener que preguntar directamente.

Ultima verificacion: 2026-10-01 (rama feature/arranque-reproducible)

## Abiertos

### Archivos del worker quedan con dueno root en Linux

**Que pasa:** el worker corre como root dentro del contenedor, asi que
en un host Linux los archivos que escribe en `data/` y `models/` quedan
con dueno root. En Mac y Windows (Docker Desktop) no ocurre.

**Mitigacion actual:** `bash demo.sh limpiar` los borra desde un
contenedor, sin necesitar sudo.

**Posible fix:** correr el worker con el UID del host (`user=` en
DockerOperator y en el servicio `worker`). Requiere mover el cache de
Ivy con los JARs de Delta a una ruta legible por cualquier usuario,
porque hoy vive en el home de root dentro de la imagen.

### Imagenes de MinIO sin version fija

**Que pasa:** `docker-compose.yml` usa `minio/minio:latest` y
`minio/mc:latest`. Un build futuro puede traer una version con cambios
incompatibles, y no hay forma de saber que version corrio una demo.

**Posible fix:** fijar ambas a un tag `RELEASE.*` verificado, igual que
Airflow (`2.10.3`) y Postgres (`16-alpine`).

## Resueltos

### CI: el smoke test no cubria el pipeline del modelo de riesgo

Registrado el 2026-09-15 (PR #8). El job `pipeline-smoke-test` corria
Bronze -> Silver -> Gold pero no `generate_labels.py` ->
`train_model.py` -> `predict_risk.py`, asi que un cambio en
`risk_features.py` o `kpi_definitions.py` podia romper el modelo sin
que CI lo detectara.

Resuelto en `feature/arranque-reproducible`: el smoke test corre los
tres pasos del modelo y verifica que `probabilidad_impago` tenga valor
en [0, 1] para todos los clientes de Gold. El volumen por default del
smoke test subio de 20 a 100 clientes, porque con 20 el split
estratificado 80/20 puede dejar el conjunto de prueba sin positivos.

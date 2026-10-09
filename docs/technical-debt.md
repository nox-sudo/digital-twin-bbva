# Deuda tecnica conocida

Registro de gaps conocidos que no bloquean el estado actual del pipeline,
pero que un futuro colaborador (o evaluador) deberia poder encontrar aca,
sin tener que preguntar directamente.

Ultima verificacion: 2026-10-08 (rama docs/mediciones-auc-pruebas-respaldo)

## Abiertos

### Las etiquetas del modelo de riesgo son circulares

**Que pasa:** `generate_labels.py` calcula la etiqueta a partir de 3 variables
(`ratio_endeudamiento`, `capacidad_ahorro`, `uso_linea_credito`) del mismo
feature store con el que se entrena el modelo. El ruido de 8 % (XOR) no rompe
esa dependencia: solo le pone un techo al AUC. El modelo sigue reconstruyendo la
regla, y su SHAP lo confirma (esas 3 variables encabezan la importancia).

Medido el 2026-10-08 con 500 clientes y 76 positivos, validacion cruzada
estratificada de 5 pliegues repetida 10 veces, con el mismo XGBoost de
`train_model.py` (reproducible con `experimentos/auc_circularidad.py`; ver su
docstring para correrlo en el contenedor):

| Medida | AUC |
|---|---|
| Modelo completo, validacion cruzada | 0.81 (0.785 a 0.825 entre repeticiones) |
| Techo con la regla exacta (etiqueta con ruido contra regla sin ruido) | 0.82 |
| Solo con las 3 variables de la etiqueta | 0.82 |
| Sin esas 3 variables | 0.70 |
| Prediccion del modelo contra la regla sin ruido | 0.97 |

El modelo completo llega casi al techo, asi que el AUC mide cuanto ruido se
metio al generar las etiquetas, no poder predictivo. Sin las 3 variables el AUC
no cae a 0.5: las demas features (ingreso, gasto, saldo) comparten informacion
con los KPIs de los que sale la regla; es una hipotesis, no esta verificada.

Un AUC de un solo split 80/20 (100 clientes de prueba, 15 positivos) tiene una
desviacion de unos 0.06 y dio 0.73, 0.85 y 0.93 en tres corridas con datos
distintos. Ninguno de esos numeros es informativo por si solo; no deben citarse
sin intervalo.

**Posible fix:** separar en el tiempo. Las features salen de los meses 1 a 12 y
el impago se genera como un evento posterior (mes 13 en adelante) con un proceso
latente: una parte depende del comportamiento observable y otra de factores que
el modelo no ve (choques de ingreso, un rasgo oculto por cliente). Se valida
fuera de tiempo, que es como se valida un modelo de credito. Implica cambios en
el generador y en las sesiones de datos.

### test_model.py no corre en macOS sin libomp

**Que pasa:** `tests/test_model.py` importa XGBoost, que en macOS necesita la
libreria `libomp` (no viene con el sistema). En esta maquina no esta instalada, asi
que esa prueba no corre en local; en CI (Linux) corre sin pasos extra.

**Posible fix:** instalar `libomp` (Homebrew: `brew install libomp`) o marcar la
prueba para que se omita en macOS con un mensaje claro.

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

### Las entregas nuevas solo agregan transacciones

**Que pasa:** una entrega mensual (`demo.sh nueva-entrega`) trae un mes
mas de transacciones, pero los snapshots de clientes, cuentas y CETES
son identicos a la entrega base: no hay clientes nuevos, bajas ni
saldos que cambien. Silver y Bronze ya soportan esos cambios (Bronze
acumula versiones, Silver toma la mas reciente por llave), pero el
generador no los produce.

**Posible fix:** en cada mes adicional, agregar algunos clientes nuevos
(al final, para no alterar los existentes) y recalcular saldo_actual a
partir de los movimientos del mes.

### Corrupcion estructural de archivos no se detecta en Bronze

**Que pasa:** el registro de calidad cubre valores invalidos dentro de
filas bien formadas. Una linea de CSV mal formada (comillas sin cerrar,
mas o menos columnas que el encabezado) la interpreta Spark en modo
permisivo: los campos faltantes quedan NULL (y si se detectan despues,
como not_null o tipo) pero los campos de mas se descartan sin aviso.

**Posible fix:** leer los CSV de Bronze con esquema explicito y
columnNameOfCorruptRecord, para conservar la linea original en
_corrupt_record y registrarla como incidencia estructural.

## Resueltos

### pytest fuera del venv fallaba en las pruebas de Spark

Registrado el 2026-10-08. Correr `.venv/bin/python -m pytest` sin el venv en el
PATH hacia que los workers de Spark lanzaran el `python3` del sistema (3.9, sin
pyspark), y 17 pruebas fallaban con "Error from python worker". Se diagnostico
primero, mal, como un problema de Java; Java estaba bien. Con `uv run pytest`
(o con el venv activo) nunca fallaba, y por eso el CI no lo veia.

Resuelto en `tests/conftest.py`: `PYSPARK_PYTHON` y `PYSPARK_DRIVER_PYTHON` apuntan
al mismo interprete que corre pytest. Verificado en ambas formas: 68 pruebas
pasan (sin `test_model.py` ni `test_landing.py`, que necesitan `libomp` y
`boto3` en el entorno local).

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

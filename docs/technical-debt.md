# Deuda tecnica conocida

Registro de gaps conocidos que no bloquean el estado actual del pipeline,
pero que un futuro colaborador (o evaluador) deberia poder encontrar aca,
sin tener que preguntar directamente.

Ultima verificacion: 2026-10-09 (rama feature/etiquetas-evento-posterior)

## Abiertos

### Etiquetas de impago como evento posterior: evaluacion pendiente

**Estado:** el diseno de las etiquetas cambio y esta implementado y probado, pero
su evaluacion con XGBoost en 500 y en 5,000 clientes todavia no se corre (necesita
`libomp` en macOS). Hasta entonces no se cita ningun AUC del diseno nuevo.

**Por que cambio.** La etiqueta anterior (`generate_labels.py` con la regla "al
menos 2 de 3 condiciones" mas un XOR de ruido de 8 %) era una funcion de 3
variables (`ratio_endeudamiento`, `capacidad_ahorro`, `uso_linea_credito`) del
mismo feature store con el que se entrena el modelo. El ruido no rompia esa
dependencia, solo le ponia un techo al AUC. Medido el 2026-10-08 con 500 clientes
y 76 positivos, validacion cruzada de 5 pliegues repetida 10 veces:

| Medida (diseno anterior) | AUC |
|---|---|
| Modelo completo, validacion cruzada | 0.81 (0.785 a 0.825 entre repeticiones) |
| Techo con la regla exacta (etiqueta con ruido contra regla sin ruido) | 0.82 |
| Solo con las 3 variables de la etiqueta | 0.82 |
| Sin esas 3 variables | 0.70 |
| Prediccion del modelo contra la regla sin ruido | 0.97 |

El modelo llegaba casi al techo: el AUC medido era cuanto ruido se habia metido,
no poder predictivo. Ese diseno vive en el tag `v0.3.0` (el `generate_labels.py`
con XOR) y el script del experimento original esta en el commit `3423e36`
(`experimentos/auc_circularidad.py`, ya retirado del arbol actual).

**Diseno nuevo.** El impago es un evento posterior a las features, generado con un
modelo latente (`src/gold/etiquetas_impago.py`, parametros en
`config/etiquetas_impago.yaml`, tabla `gold_etiquetas_impago`):

    logit(p) = b0 + b1*ratio_endeudamiento + b2*uso_linea_credito
                  - b3*capacidad_ahorro + b4*z + b5*choque

Las 3 variables van winsorizadas en p1/p99 y estandarizadas sobre todos los
clientes. `z ~ N(0,1)` es un rasgo oculto por cliente y `choque` indica si hubo al
menos un choque mensual (probabilidad `q`) en los 6 meses del horizonte; ninguno
esta en el feature store. `b0` se calibra por biseccion a la tasa objetivo (0.15,
elegida para tener mas positivos con los que evaluar, no para parecerse a la tasa
anterior, que venia inflada por el XOR). Los parametros se fijaron antes de ver
ningun resultado; si se cambian despues, se documenta como una iteracion aparte.

- **Clientes sin producto.** `ratio_endeudamiento` y `uso_linea_credito` valen 0
  en el feature store para quien no tiene prestamo o tarjeta: es ausencia de
  exposicion, no un dato faltante. Se dejan en 0 antes de winsorizar y
  estandarizar, asi que ese cliente recibe menos riesgo por ese canal pero
  conserva el de capacidad de ahorro, rasgo oculto y choque.
- **Fuera de Gold.** `z`, `choque`, `logit` y `p` no estan en ninguna tabla de Gold
  (el CI lo verifica sobre el DuckDB real). Se guardan para auditoria en
  `data/auditoria/etiquetas_latentes.parquet`, fuera de las sesiones, de la landing
  y del respaldo: se regeneran con la semilla y las features.

**Limites del diseno**
- **Ventana.** `fecha_observacion` es la fecha de corte de las features
  (`fecha_corte` en `gold_features_cliente`); el impago se simula en los 6 meses
  siguientes. Cada `nueva-entrega` avanza esa fecha y regenera las etiquetas. Los
  meses que llegan despues no reflejan los choques simulados: el generador de
  transacciones no sabe del evento. Las etiquetas de una entrega nueva no son
  hechos historicos sino una muestra nueva del mismo proceso.
- **Independencia.** `z` y `choque` son independientes de las features. En la
  realidad los rasgos ocultos se correlacionan con lo observable.
- **Coeficientes.** Son plausibles, no calibrados contra datos reales.
- **500 clientes** dejan unos 15 positivos en una prueba 80/20: por eso se evalua con
  validacion cruzada y se reportan ambos tamanos.

**Por que se prueban signos y orden, no magnitudes.** Una regresion logistica sobre
las 3 variables observadas omite `z` y `choque`, que son independientes de ellas.
Aun asi no recupera los coeficientes verdaderos sino unos mas chicos en valor
absoluto: el enlace logistico no es colapsable, asi que omitir un predictor
independiente encoge los demas (el modelo marginal no es el modelo completo). La
aproximacion de Zeger, Liang y Albert (1988) da

    estimado / verdadero ~ 1 / sqrt(1 + c^2 * var_omitida),   c = 16*sqrt(3)/(15*pi)

con `var_omitida = b4^2 + b5^2 * q_h * (1 - q_h)`. Con la configuracion actual es
0.892. Se verifico con 100,000 clientes simulados: estimado/verdadero fue 0.89
(ratio de endeudamiento), 0.90 (uso de linea) y 0.91 (capacidad de ahorro), y con
menos clientes oscila alrededor de eso (0.84 a 0.96 con 5,000 a 20,000). La
prueba `test_la_atenuacion_teorica_coincide_con_la_observada` lo comprueba. Como
el encogimiento es casi igual para todos los coeficientes, sobreviven el signo y
el orden (b1 > b3 > b2), y eso es lo que se prueba; las magnitudes no tienen por
que coincidir.

**Pendiente.** Correr `experimentos/auc_evento_posterior.py` con XGBoost en 500 y en
5,000 clientes y publicar aqui: validacion cruzada 5x5, AUC del oraculo (la
probabilidad verdadera), comparacion de signos y orden, y los 3 AUC del mismo
entrenamiento repetido. Para 5,000 clientes se corre el pipeline con otra sesion y
otro `DATA_ROOT`, sin tocar `data/` de la instancia.

**Como leer un AUC.** El que imprime `train_model.py` sale de un solo split 80/20
(con 500 clientes, 100 de prueba y unos 15 positivos): tiene una desviacion de
unos 0.06 y dio 0.73, 0.85 y 0.93 en tres corridas con datos distintos. Ninguno
de esos numeros es informativo por si solo; no deben citarse sin intervalo.

### test_model.py no corre en macOS sin libomp

**Que pasa:** `tests/test_model.py` importa XGBoost, que en macOS necesita la
libreria `libomp` (no viene con el sistema). Sin ella el import lanza
`XGBoostError`; en CI (Linux) corre sin pasos extra.

**Mitigacion:** la prueba se omite a nivel de modulo, con un aviso que indica
`brew install libomp`, solo en macOS y solo cuando el error menciona `libomp`.
Cualquier otro fallo de import se sigue viendo (verificado con una mutacion de la
condicion). Un `skipif` normal no sirve: el import falla antes de recolectar.

**Fix completo:** instalar `libomp` (Homebrew: `brew install libomp`) para que la
prueba corra tambien en local.

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

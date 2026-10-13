# Deuda tecnica conocida

Registro de gaps conocidos que no bloquean el estado actual del pipeline,
pero que un futuro colaborador (o evaluador) deberia poder encontrar aca,
sin tener que preguntar directamente.

Ultima verificacion: 2026-10-13 (rama fix/guardas-etiquetas)

## Abiertos

### Etiquetas de impago como evento posterior

**Estado:** el diseno de las etiquetas cambio, esta implementado y probado, y se
evaluo con XGBoost en 500 y en 5,000 clientes (2026-10-13, resultados abajo). La
evaluacion corrio en el worker Linux de Docker, no en macOS, porque `libomp` sigue
sin instalarse en la maquina de desarrollo. Los bordes que encontraron las pruebas de
limites ya tienen guarda (ver "Resueltos").

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

**Evaluacion (2026-10-13).** Los parametros de `config/etiquetas_impago.yaml` son
los fijados el 2026-10-09: el archivo no cambia desde su commit de creacion
(`2aef649`) y no se ajusto nada despues de ver estos numeros.

Condiciones: codigo de `develop` en `3056b54`, imagen `gemelo-worker` construida de
ahi (Linux, XGBoost 3.2.0), sesion con semilla 42, fecha de referencia 2026-09-30 y
12 meses, `PII_HASH_SALT` de prueba, cada tamano en su propio volumen de datos (la
instancia viva no se toco). XGBoost con los hiperparametros de `train_model.py`
(200 arboles, profundidad 4, tasa de aprendizaje 0.05, `scale_pos_weight`, semilla 42).
Comando: `experimentos/auc_evento_posterior.py`; validacion cruzada estratificada de
5 pliegues repetida 5 veces.

| Medida | 500 clientes (79 positivos, 15.8 %) | 5,000 clientes (768 positivos, 15.4 %) |
|---|---|---|
| AUC, validacion cruzada (por pliegue) | 0.728 +- 0.060 (0.594 a 0.839) | 0.776 +- 0.016 (0.743 a 0.807) |
| AUC fuera de pliegue, media de 5 repeticiones | 0.725 (0.676 a 0.753) | 0.775 (0.772 a 0.778) |
| AUC del oraculo (probabilidad verdadera `p`) | 0.860 | 0.845 |
| Hueco oraculo - modelo | 0.135 | 0.069 |
| Sin las 3 variables observadas (ablacion) | 0.657 (0.597 a 0.683) | 0.734 (0.726 a 0.739) |
| 3 entrenamientos con la misma semilla y el mismo split | 0.781250 los tres | 0.739446 los tres |

El modelo queda por debajo del oraculo en los dos tamanos, como debe: no ve `z` ni
`choque`. El hueco se achica con mas clientes pero no se cierra a 5,000. Los tres
entrenamientos repetidos dan el mismo AUC (determinismo), y ese AUC de un solo split
(0.78 y 0.74) cae a ambos lados del de validacion cruzada (0.725 y 0.775): el split
unico no es una medida confiable, la validacion cruzada si.

Signos y orden contra los coeficientes verdaderos (regresion logistica sobre las 3
variables estandarizadas; el factor teorico de atenuacion es 0.892):

| Variable | Verdadero | 500: estimado (razon) | 5,000: estimado (razon) |
|---|---|---|---|
| `ratio_endeudamiento` | +0.80 | +0.82 (1.02) | +0.77 (0.96) |
| `uso_linea_credito` | +0.50 | +0.57 (1.14) | +0.42 (0.84) |
| `capacidad_ahorro` | -0.70 | -0.66 (0.94) | -0.61 (0.88) |

Los signos coinciden en ambos tamanos y el orden verdadero (`ratio_endeudamiento` >
`capacidad_ahorro` > `uso_linea_credito`) se recupera en ambos. Con 500 clientes las
razones se dispersan alrededor de 1 (de 0.94 a 1.14), mas ruido que atenuacion; con
5,000 quedan por debajo de 1, de 0.84 a 0.96, en el rango que predice la teoria. Es una
metrica de recuperacion, no una prueba.

La ablacion no mide "cuanta senal queda sin las 3 variables": quitarlas de la matriz
no las quita de la informacion. `capacidad_ahorro` es exactamente
`ingreso_mensual_promedio - gasto_mensual_total`, y `ratio_endeudamiento` es deuda
entre `ingreso_mensual_promedio`; el ingreso y el gasto siguen en la matriz, asi que el
modelo puede reconstruir esa senal. Por eso se agrego la ablacion estricta (E6, agregada
despues de ver E5, no estaba en el diseno original), que quita tambien
`ingreso_mensual_promedio`, `gasto_mensual_total` y `gasto_promedio_3_meses`:

| Ablacion (AUC fuera de pliegue, media de 5 repeticiones) | 500 clientes | 5,000 clientes |
|---|---|---|
| Completo | 0.725 | 0.775 |
| E5: sin las 3 variables | 0.657 | 0.734 |
| E6: sin las 3, ingreso ni gasto | 0.614 (0.576 a 0.647) | 0.689 (0.685 a 0.694) |

Cada ablacion baja el AUC, pero con 5,000 clientes todavia queda en 0.689. Las demas
features (saldo liquido, estabilidad del ingreso, frecuencia de transacciones, edad,
categoria de gasto) siguen correlacionadas con el nivel de ingreso y de endeudamiento,
y el modelo las usa como sustitutos. No es una fuga: `z` y `choque` no estan en ninguna
tabla de Gold (el CI lo verifica). Es una consecuencia de que las features sinteticas
no son independientes entre si, que es lo normal.

**AUC contra el tamano de la muestra.** `experimentos/limites_etiquetas.py --parte
tamano` saca 20 submuestras al azar de las features y etiquetas de los 5,000 clientes y
mide el AUC fuera de pliegue (5 pliegues) de cada una:

| Clientes | Submuestras evaluables | Positivos | AUC del modelo (media, desv., min a max) | AUC del oraculo |
|---|---|---|---|---|
| 50 | 18 de 20 | 5 a 13 | 0.617, 0.151 (0.327 a 0.848) | 0.858 |
| 100 | 20 de 20 | 9 a 21 | 0.694, 0.121 (0.497 a 0.926) | 0.852 |
| 200 | 20 de 20 | 22 a 44 | 0.701, 0.106 (0.520 a 0.880) | 0.839 |
| 500 | 20 de 20 | 68 a 90 | 0.723, 0.038 (0.647 a 0.807) | 0.847 |
| 1,000 | 20 de 20 | 129 a 172 | 0.735, 0.031 (0.681 a 0.790) | 0.838 |
| 2,500 | 20 de 20 | 354 a 407 | 0.756, 0.013 (0.734 a 0.783) | 0.844 |
| 5,000 | 1 de 1 | 768 | 0.773 | 0.845 |

Lectura: el AUC medio sube de 0.62 a 0.77 al pasar de 50 a 5,000 clientes y no se
estabiliza ahi: el modelo sigue aprendiendo, y por eso el AUC de 500 clientes
subestima lo que el diseno permite. La dispersion tambien baja, de 0.15 a 0.013. Con
100 clientes (el volumen del smoke de CI) una sola medicion varia entre 0.50 y 0.93,
por encima incluso del oraculo (0.85) en algunas submuestras; con 50 no siempre se
puede evaluar (2 de 20 submuestras tuvieron menos de 5 positivos). Por eso el smoke
de CI verifica que el pipeline corra y que las probabilidades esten en [0, 1], y no
un umbral de AUC; para comparar modelos se necesitan al menos 500 clientes y
validacion cruzada.

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

### Etiquetas de impago: entradas y configuraciones sin guarda

Registrado el 2026-10-13 con `experimentos/limites_etiquetas.py --parte bordes`
(configuracion del repo, entradas sinteticas con las formas de las features reales).
Ninguno afectaba al pipeline con datos normales; eran casos en los que el modulo no
fallaba con un mensaje claro o no avisaba de una etiqueta inservible.

Resuelto en `fix/guardas-etiquetas`, una guarda y al menos una prueba por caso:

| Caso | Antes | Ahora |
|---|---|---|
| 0 clientes | `IndexError` crudo | `ValueError: el feature store no tiene clientes` |
| Tasa objetivo extrema con pocos clientes (`1e-6`, `0.999999`) | 0 positivos, o todos, escritos en Gold sin aviso | `generate_labels.py` falla antes de escribir si alguna clase tiene menos de 2 clientes |
| Menos de 30 positivos | sin aviso | Advertencia en el log: se puede entrenar, pero el AUC oscila demasiado |
| Coeficiente `NaN` o infinito en el YAML | `no se pudo acotar b0`, sin senalar el coeficiente | `cargar_config` nombra el coeficiente y pide un numero finito |
| Semilla negativa | error de NumPy | `cargar_config` pide una semilla >= 0 |
| Infinitos en una feature | uno solo se absorbia en silencio; varios daban `no se pudo acotar b0` | `ValueError: valores infinitos en [...]`, antes de winsorizar |

Verificado con mutaciones: al revertir cada guarda, falla la prueba correspondiente.

Lo que ya resistia sin cambios: 1, 2 y 10 clientes (generan, aunque sin sentido
estadistico), columnas constantes y nadie con producto (quedan en 0, sin dividir
entre 0), un ratio de `1e12`, `NaN` en `ratio_endeudamiento` (se trata como "sin
prestamo", por diseno), ids repetidos y columnas faltantes (errores claros),
coeficientes multiplicados por 50 (`b0` = -80, tasa exacta), todos los coeficientes en 0,
sin choques y con choques casi seguros, horizontes de 1 y de 120 meses, winsorizar en
(0, 1) y en (0.49, 0.51), y tasas objetivo de 0.05 a 0.95. En todas la probabilidad media
coincide con la tasa objetivo (a 6 decimales). Escala: 50,000 clientes en 0.03 s y 500,000
en 0.30 s, con un pico de 320 MB; la etiqueta no es el cuello de botella.

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

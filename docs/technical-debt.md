# Deuda tecnica conocida

Registro de gaps conocidos que no bloquean el estado actual del pipeline,
pero que un futuro colaborador (o evaluador) deberia poder encontrar aca,
sin tener que preguntar directamente.

Ultima verificacion: 2026-10-16 (rama fix/auditoria-oct)

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

### Probabilidad de impago descalibrada por scale_pos_weight

**Que pasa:** `train_model.py` entrena con `scale_pos_weight` (negativos entre
positivos, ~6). Eso sube la sensibilidad del modelo, pero sus probabilidades
dejan de estar calibradas: con la sesion base, la media publicada en Gold es
0.205 contra una tasa real de 0.164, y 14 clientes quedan por encima de 0.90.
El dashboard muestra esa salida como "probabilidad de impago".

**Posible fix:** quitar el peso y comparar AUC fuera de pliegue (el ordenamiento
casi no deberia cambiar), o calibrar despues (`CalibratedClassifierCV`, isotonica)
sobre las probabilidades fuera de pliegue. Cambia un hiperparametro fijado el
2026-10-09, asi que la evaluacion se vuelve a correr y se reporta junto a la actual.

### Las entregas nuevas solo agregan transacciones

**Que pasa:** una entrega mensual (`demo.sh nueva-entrega`) trae un mes
mas de transacciones, pero los snapshots de clientes, cuentas y CETES
son identicos a la entrega base: no hay clientes nuevos, bajas ni
saldos que cambien. Silver y Bronze ya soportan esos cambios (Bronze
acumula versiones, Silver toma la mas reciente por llave), pero el
generador no los produce.

**Posible fix:** en cada mes adicional, agregar algunos clientes nuevos
(al final, para no alterar los existentes) y recalcular saldo_actual a
partir de los movimientos del mes. Ojo: el generador hoy se niega a
activar una entrega que cambie cualquier archivo ya entregado
(`verificar_continuacion` en `src/common/sesiones.py`), y eso incluye
`clientes.csv` y `cuentas.json`. Si estas entidades pasan a cambiar entre
entregas, hay que decidir de forma explicita como versionarlas.

## Resueltos

### El PSI reacciona tarde a una deriva reciente

**Que pasa:** encontrado al construir el escenario de deriva del generador
(2026-10-16). Casi todas las features de `gold_features_cliente` promedian
todo el historial (rejilla cliente x mes completa), asi que un cambio en los
ultimos meses se diluye en el promedio y el PSI tarda mucho en verlo. Con
un deterioro de 5 % de ingreso al mes en el 40 % de los clientes, el PSI del
ingreso promedio seguia en 0.037 (estable) tras 12 entregas, mientras que el
del gasto de los ultimos 3 meses, la unica feature de ventana reciente,
llegaba a 0.081 con los mismos datos. Esos valores son aproximados con los CSV; con el
pipeline y el escenario de ejemplo (60 % / -10 % / +8 %, 6 entregas,
2026-10-18) el ingreso promedio quedo en 0.036 y el gasto de los ultimos 3
meses en 0.186, las mismas cifras que la aproximacion. Detalle en
`docs/monitoreo-deriva.md`.

Es una limitacion del detector, no del escenario: una cartera que se
deteriora mes a mes pasaria meses sin alerta.

**Posible fix:** medir el PSI tambien sobre features de ventana reciente
(por ejemplo, ingreso, gasto y capacidad de ahorro de los ultimos 3 meses)
y no solo sobre las del modelo. Se cruza con el rediseno de la ventana de
features de la etiqueta: si las features del modelo pasan a una ventana
movil, el mismo PSI reacciona antes. Requiere nuevos KPIs en Gold y una
referencia nueva (`monitor_drift.py --nueva-referencia`).

**Resuelto en buena parte (2026-10-19):** los KPIs de flujo y la proporcion de
retiros se calculan sobre una ventana de 12 meses hasta la fecha de corte
(`config/features.yaml`). Con la aproximacion sobre las sesiones de la
instancia, en la entrega 7 el ingreso pasa de 0.049 a 0.113 y la capacidad de
ahorro de 0.311 a 0.654. La ventana tambien diluye, pero en 12 meses y no en
todo el historial. Medido con el pipeline el 2026-10-20 sobre la instancia
reconstruida con la ventana (7 entregas): mismas cifras; tabla completa en
`docs/monitoreo-deriva.md`.

### Features que dependen del tiempo derivan sin cambio de poblacion

**Que pasa:** encontrado al medir el escenario de deriva con el pipeline
(2026-10-18, 6 entregas). Contra la referencia congelada, `antiguedad_dias`
llego a PSI 1.007 y `proporcion_retiros` a 0.556, las dos en "reentrenar",
sin que el escenario las toque:

- `antiguedad_dias` se mide contra la fecha de corte, que avanza un mes por
  entrega: toda la distribucion se desplaza ~30 dias por mes.
- `proporcion_retiros` se calcula sobre todo el historial y se concentra al
  acumular meses (clientes con cero retiros: 42 en la base, 12 tras 6
  entregas). Con y sin escenario da el mismo 0.556.

El PSI del score (0.351) mezcla esta deriva con la del escenario. En una
cartera real, una referencia congelada contra estas variables alertaria
aunque la poblacion no cambiara: falsos positivos que restan credibilidad a
las alertas verdaderas.

**Posible fix:** cualquiera de estas, o una combinacion:

- Medir las variables de antiguedad descontando el tiempo transcurrido desde
  la referencia (antiguedad a la fecha de corte de la referencia), o en
  meses desde el alta agrupados en tramos que no cambian con el corte.
- Calcular las proporciones y promedios sobre una ventana fija (por ejemplo,
  los ultimos 12 meses) en vez de todo el historial. Ataca tambien la
  dilucion ("El PSI reacciona tarde a una deriva reciente").
- Marcar en la configuracion las variables que dependen del tiempo y
  reportarlas aparte, sin alerta, mientras no se redisenen.

Cambiar las features del modelo afecta al entrenamiento y a la referencia,
asi que va junto con el rediseno de la ventana de features.

**Mitigacion aplicada (2026-10-19):** la tercera opcion. `antiguedad_dias`,
`edad_anios` y `proporcion_retiros` estan en `variables_temporales`
(`config/monitoreo_psi.yaml`); su PSI se guarda con estado `temporal`, sin
alerta. Sigue abierto el fix de fondo (ventana fija).

**Resuelto para la proporcion de retiros (2026-10-19):** con la ventana de 12
meses deja de concentrarse al acumular historial (aproximacion en la entrega 7:
0.930 sobre todo el historial, 0.047 con la ventana) y salio de
`variables_temporales`. Sigue abierto para `antiguedad_dias` y `edad_anios`,
que se miden contra la fecha de corte por definicion: quedan como `temporal`.
Si hiciera falta medirlas, la opcion es la primera de arriba (tramos que no
cambian con el corte).

### Corrupcion estructural de archivos no se detecta en Bronze

Hallazgo 5 de la auditoria del 2026-10-14. Una linea de CSV mal formada
(comillas sin cerrar, mas o menos columnas que el encabezado) la leia Spark
en modo permisivo: los campos faltantes quedaban NULL, los de mas se
descartaban sin aviso y una comilla sin cerrar se mezclaba con el campo
siguiente.

Resuelto el 2026-10-19: `ingest_bronze.leer_csv` lee los CSV con un esquema de
texto tomado del encabezado de cada archivo, mas `_corrupt_record`, en modo
`PERMISSIVE`. Las columnas salen del encabezado y no del codigo, asi que una
columna nueva del origen sigue entrando por `mergeSchema`; archivos con
encabezados distintos se leen por separado y se unen por nombre. Bronze
guarda la linea original (es la zona autorizada a tener PII en claro) y el log
de ingesta cuenta las lineas mal formadas por archivo. En Silver,
`marcar_estructura` la convierte en la violacion `estructura` y descarta
`_corrupt_record` antes de cualquier escritura: la fila va a cuarentena y el
registro de calidad guarda regla, llave y archivo, nunca el valor.

Verificado con Spark 4.1.1 y Delta en el worker: una linea con una columna de
mas en un CSV de transacciones queda en Bronze con su texto, las otras 10,533
filas del archivo intactas, y Silver la deja en cuarentena con motivo
`estructura_registro` sin `_corrupt_record` en Silver ni en la cuarentena.
Pruebas: `tests/test_bronze_estructura.py` y un paso del smoke test.

Limites: un encabezado mal formado no se detecta asi, porque es la base del
esquema; si le falta una columna, la detiene `SchemaValidationError` en
Silver. Cambio de comportamiento: una fila truncada a la que le falta una
columna opcional (por ejemplo `email`) ahora va a cuarentena; un campo vacio
con su separador sigue siendo valido.

### Sesiones distintas segun la zona horaria de la maquina

Encontrado el 2026-10-16 al correr la primera entrega con escenario de
deriva: la regla de continuacion del generador
(`verificar_continuacion`) se nego a activar la entrega porque
`clientes.csv` cambiaba. La causa no era el escenario: `fecha_entre()` usaba
`fake.date_between_dates()`, que convierte el instante sorteado a la zona
horaria del proceso, y para fechas anteriores a 1970 (timestamp negativo)
el resultado depende de esa zona. La sesion base, generada en una Mac en
UTC-7, y la misma sesion generada en el worker (UTC) diferian en la fecha de
nacimiento de 34 de 500 clientes, un dia antes en UTC, y por lo tanto en su
CURP (34) y RFC (27). Las corridas anteriores del DAG no lo notaban porque
reutilizaban la sesion ya generada. El CI tampoco, porque genera dos veces
en la misma maquina.

Sin la regla de continuacion, la entrega habria publicado un `clientes.csv`
distinto y Silver se habria quedado con las fechas y CURP nuevas sin aviso.

Fix: `fecha_entre()` interpreta la fecha en UTC (`tzinfo` explicito). El
sorteo es el mismo, asi que solo cambian esas 34 fechas respecto a una
sesion generada en UTC-7; la salida es identica a la que ya producia el
worker. `test_misma_sesion_en_cualquier_zona_horaria` genera la misma
sesion en tres zonas (UTC, America/Mazatlan, Asia/Tokyo) en procesos
separados y exige archivos identicos; con el codigo anterior falla.

### Imagenes sin version fija (MinIO y worker)

Senalado en la auditoria del 2026-10-14 (hallazgo 6). Resuelto en
`fix/auditoria-oct` el 2026-10-16: las cuatro imagenes quedan fijas a lo que ya
corria en la instancia verificada.

| Imagen | Antes | Ahora | De donde sale el valor |
|---|---|---|---|
| `minio/minio` | `:latest` | `@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e` | `docker image inspect`; es la imagen del contenedor `minio` en ejecucion (mismo id) |
| `minio/mc` | `:latest` | `@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727` | `docker image inspect`; es la imagen de `minio-createbucket` (mismo id) |
| `python` (base del worker) | `3.11-slim` | `3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce` | `docker pull` del 2026-10-16; sus 4 capas son el inicio de las 12 del worker en ejecucion, asi que es la misma base |
| `uv` (copiado al worker) | `:latest` | `:0.13.0` | `uv --version` dentro del worker en ejecucion. El `latest` de ese dia ya era 0.13.1; se fijo la version que corria, no la mas nueva |

Verificacion: el worker se reconstruyo con estas fijaciones bajo una etiqueta
temporal (sin tocar `gemelo-worker:local`) y termino bien: `uv 0.13.0`, Python
3.11.17, XGBoost 3.2.0 y PySpark 4.1.1.

Para actualizar una imagen: bajar la nueva, tomar su digest o version con
`docker image inspect` o `--version`, reconstruir y correr el smoke test. Un tag
inventado rompe el build, por eso no se fija sin esa verificacion. Airflow
(`2.10.3`) queda fijado por version. Postgres usaba `16-alpine`, una etiqueta
que se mueve con cada version menor; desde el 2026-10-19 va por digest
(`postgres:16-alpine@sha256:57c72fd2...`), la imagen del contenedor en
ejecucion (PostgreSQL 16.14, mismo id en `docker image inspect`). Queda sin
fijar la version de `uv` que instala el CI con `astral-sh/setup-uv@v3`.

### Datos sinteticos sin perfil por cliente

Registrado el 2026-10-16, al revisar clientes uno por uno en el dashboard: CLI-000001
cobraba nomina en 5 de 12 meses y su "ingreso mensual" de 40,840 promediaba solo
esos meses. La causa estaba en el generador: cada mes era un sorteo independiente.
La regla "75 % son empleados formales" se sorteaba por cliente y por mes, la nomina
era uniforme entre 4,000 y 25,000 sin relacion con el ingreso declarado, y compras,
pagos y retiros tampoco dependian del cliente.

Resuelto en `fix/datos-perfil-cliente`:

- **Generador.** `generar_perfiles` sortea una vez por cliente: ingreso formal (nomina
  quincenal fija) o informal (2 a 6 depositos al mes, tipo nuevo `deposito`, con
  volatilidad propia), anclado al ingreso declarado; proporcion de gasto (15 % gasta
  de forma cronica mas de lo que gana); preferencia por categorias; puntualidad de
  pago y cuota fija por deuda. Usa un generador aleatorio propio, y la semilla por mes
  sigue garantizando que una entrega nueva no altera los meses anteriores.
- **Cuentas.** Saldo de la cuenta digital, linea de tarjeta (1 a 3 veces el ingreso)
  y monto de prestamo (cuota de 10 % a 30 % del ingreso) escalan con el ingreso
  declarado. Antes se sorteaban sin relacion: con el perfil ya corregido, eso dejaba
  clientes con cuotas mayores que su ingreso (CLI-000001 pagaba 21,000 al mes de
  deuda con 22,000 de ingreso). Clientes y CETES no cambian.
- **KPIs.** Ingreso, gasto, estabilidad y actividad P2P se promedian sobre la rejilla
  completa cliente x mes (un mes sin movimientos vale 0). El ingreso incluye
  `deposito`. `capacidad_ahorro` descuenta compras, pagos de deuda y retiros; antes
  solo compras.
- **Score.** `train_model.py` guarda la probabilidad fuera de pliegue (5 pliegues) y
  `predict_risk.py` la publica para los clientes con etiqueta. Antes se predecian con
  el modelo final, que habia visto su etiqueta: 58 clientes salian por encima de 0.90,
  ahora 14 (ver "Probabilidad de impago descalibrada" en Abiertos para el resto).

Medido sobre los CSV de la sesion base (500 clientes, semilla 42), antes y despues:

| Medida | Antes | Despues |
|---|---|---|
| Meses-cliente sin ingreso | 23.9 % | 3.0 % (informales con un mes seco) |
| Correlacion ingreso declarado / observado | -0.03 | 0.97 |
| Coeficiente de variacion mensual del ingreso (mediana) | 0.75 | 0.21 (formales 0, informales 0.2 a 0.45) |
| Correlacion compras / ingreso entre clientes | -0.03 | 0.89 |
| Misma categoria dominante en las dos mitades del ano | 12 % (azar con 12 categorias) | 64 % |
| Clientes con flujo neto negativo | 68 % | 23 % |

Evaluacion del modelo con los mismos parametros de etiqueta (sin cambios desde
2026-10-09), 500 clientes, `experimentos/auc_evento_posterior.py`:

| Medida | Datos anteriores (79 positivos) | Datos con perfil (82 positivos) |
|---|---|---|
| AUC, validacion cruzada (por pliegue) | 0.728 +- 0.060 | 0.741 +- 0.040 |
| AUC fuera de pliegue, media de 5 repeticiones | 0.725 (0.676 a 0.753) | 0.738 (0.726 a 0.748) |
| AUC del oraculo | 0.860 | 0.864 |
| Hueco oraculo - modelo | 0.135 | 0.126 |
| Sin las 3 variables observadas (ablacion) | 0.657 | 0.681 |

El cambio en AUC es pequeno y no es el objetivo: el objetivo era que los datos
describan clientes coherentes. Lo que si cambia es la estabilidad de la medida (la
desviacion entre pliegues baja de 0.060 a 0.040) y que las demas variables llevan
mas senal (la ablacion sube 2 puntos), porque la estabilidad de ingreso ahora
distingue formales de informales. Una version intermedia, con perfil pero sin
escalar las cuentas, dio 0.773: las cuotas desproporcionadas hacian el riesgo mas
facil de separar de lo que seria con datos realistas. La evaluacion a 5,000 clientes no se
repitio. Corrida fuera de Docker con Parquet en lugar de Delta (sin acceso a los
JARs de Delta en ese entorno); los numeros de Gold no dependen del formato de
almacenamiento, pero conviene repetirla en la imagen del worker.

Efecto operativo: la sesion `s42_20260930_500c_12m` conserva su id, y el generador
reutiliza una sesion existente aunque su codigo haya cambiado. Una instancia con la
sesion anterior se reconstruye desde cero (respaldo, `limpiar todo`, generador con
`--forzar`); si no, Bronze recibiria los archivos nuevos como entregas adicionales.

### Auditoria tecnica del 2026-10-14

Revision estatica del repositorio con siete hallazgos. Resueltos en
`fix/auditoria-oct`:

| Hallazgo | Antes | Ahora |
|---|---|---|
| 1. Escrituras no atomicas en Gold | `predict_risk.py` hacia DELETE y luego INSERT; `transform_gold.py` hacia DROP y luego CREATE. Un fallo entre ambos dejaba Gold sin `probabilidad_impago` o sin `gold_kpis` | Ambos en una transaccion de DuckDB con ROLLBACK. El Parquet de predicciones se escribe despues de DuckDB, a un temporal que se renombra |
| 2. Validacion de fechas contra el reloj | `date_not_future` comparaba contra `current_date()` | Compara contra la fecha de corte: el movimiento mas reciente en Bronze, o `--fecha-corte`. Sin fecha de corte (uso directo del motor) conserva el comportamiento anterior |
| 3. Inferencia con relleno permisivo | `reindex(fill_value=0)` rellenaba cualquier columna ausente, incluidas features numericas | Solo las columnas one-hot (`categoria_dominante_*`) se rellenan con 0; una feature numerica ausente lanza `ValueError` con su nombre |
| 4. Silver parcial ante un fallo | Cada entidad se sobrescribia por separado; un fallo a la mitad dejaba la capa mezclada | Antes de la corrida se guarda la version Delta de cada tabla; si algo falla, las tablas escritas vuelven a esa version (`restoreToVersion`) y las que no existian se borran |
| 7. Borde de inferencia sin pruebas | Solo carga del modelo y rango de `predict_proba` | `tests/test_predict_risk.py`: reemplazo de nulos, ROLLBACK ante un INSERT fallido y Parquet sin temporales |

Verificacion: 147 pruebas pasan. La prueba de ROLLBACK falla si se quita la
transaccion (verificado con mutacion). La reversion de Silver con Delta real
no se pudo correr fuera de Docker o del CI; la cubre el paso nuevo del smoke
test "Verificar que una corrida fallida de Silver se revierte", que corre
Silver sin la entidad transacciones y exige que clientes, cuentas y CETES
terminen en una operacion RESTORE con el mismo contenido.

Limites conocidos: la cuarentena y el registro de calidad de una corrida
fallida no se revierten (son registro de lo que paso, no la capa que lee
Gold). Si una fecha corrupta en el futuro llega a Bronze, la fecha de corte
derivada se mueve con ella; `--fecha-corte` fija el valor cuando se necesite.

El 5 se resolvio el 2026-10-19 y el 6 el 2026-10-16 (Postgres el 2026-10-19):
ver "Corrupcion estructural de archivos no se detecta en Bronze" e "Imagenes sin
version fija" mas arriba.

Fuera de la auditoria, mismo patron que el hallazgo 3: `pipeline_summary.py`
convertia cualquier error de lectura en 0 filas y podia reportar "Sin
errores". Ahora distingue tabla inexistente (0) de error de lectura, que se
muestra como tal y cambia el estado a "Lectura incompleta".

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

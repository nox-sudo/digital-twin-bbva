# Guion de la demo

Como reconstruir en cualquier maquina la instancia que se usa para presentar
y un recorrido de unos 11 minutos por el dashboard sobre ella. La instancia
es la sesion base al 30 sep 2026 (500 clientes) mas 7 entregas mensuales con
el escenario de deriva activo; el ultimo corte queda al 30 abr 2027.

Requisitos e instalacion: [ejecucion-local.md](ejecucion-local.md).

## Reconstruir la instancia

Los datos salen de la configuracion versionada (semilla 42, fecha de
referencia, 500 clientes), asi que la misma secuencia produce la misma
instancia en otra maquina. Medido en un clon limpio en la Mac (2026-11-12):
la base tardo 20 minutos (incluye construir la imagen del worker) y cada
entrega entre 1:46 y 2:25; unos 34 minutos en total.

1. **Partir de cero.** En un clon nuevo no hay nada que borrar. Si ya hay una
   instancia, respaldarla y limpiar:

   ```bash
   bash demo.sh respaldar
   bash demo.sh limpiar todo
   ```

2. **Base.** Con `config/sesion.yaml` tal como esta en el repo
   (`escenario_deriva.activo: false`):

   ```bash
   bash demo.sh
   ```

   Al despausar el DAG, Airflow corre antes una corrida calendarizada del
   ultimo mes cerrado; `demo.sh` espera a que termine y despues corre la
   suya. Las dos procesan la misma sesion.

   Opcional, para confirmar el modelo de la base (unos minutos):

   ```bash
   docker compose run --rm worker experimentos/auc_evento_posterior.py
   ```

   Debe reportar 80 positivos y AUC por pliegue 0.690.

3. **Activar el escenario de deriva.** En `config/sesion.yaml`, poner
   `escenario_deriva.activo: true`. Es un cambio local: no se sube al repo.
   Despues de la primera entrega el escenario ya no se puede cambiar (el
   generador se niega; ver [monitoreo-deriva.md](monitoreo-deriva.md)).

4. **Siete entregas**, una tras otra (cerca de 1.5 minutos cada una):

   ```bash
   for i in 1 2 3 4 5 6 7; do bash demo.sh nueva-entrega || break; done
   ```

   En macOS, con la Mac conectada a corriente y anteponiendo `caffeinate -i`
   a `bash demo.sh`: con bateria el reposo congela Docker a media corrida
   (ver Problemas comunes en [ejecucion-local.md](ejecucion-local.md)).

5. **Respaldar la instancia terminada**, para restaurarla si algo se rompe
   antes de presentar (`bash demo.sh restaurar <respaldo>` y luego
   `bash demo.sh`):

   ```bash
   bash demo.sh respaldar
   ```

6. **Crear los usuarios y abrir el dashboard.** Las contraseñas (nuevas en
   cada corrida) quedan en `data/credenciales_prueba.md`:

   ```bash
   uv run --group dashboard python generate_credentials.py
   uv run --group dashboard streamlit run dashboard/app.py
   ```

No correr otra `nueva-entrega` antes de presentar: agregaria un octavo mes y
las cifras de abajo cambiarian.

### Que tan reproducibles son las cifras

- Las de los datos (ingreso, gasto, capacidad de ahorro, flujo, PSI de las
  features) dependen solo de la configuracion y la semilla. El CI regenera
  la sesion base y compara checksums.
- Las del modelo (probabilidad de impago, bandas, PSI del score) salen de
  XGBoost con semilla fija: tres entrenamientos dan el mismo AUC. Se
  verificaron con dos reconstrucciones desde cero en un worker arm64 (Apple
  silicon). En x86_64 no se han comparado; XGBoost puede diferir en los
  ultimos decimales entre arquitecturas.
- Los ids de la referencia del PSI y las fechas de corrida llevan la hora
  de la maquina y siempre cambian.

## Recorrido

Cifras medidas en la instancia reconstruida el 2026-11-12 en un clon limpio
(despues de la correccion de coherencia temporal, PR #80), renderizando las
paginas del dashboard sobre su Gold y su modelo.

Entrar como `admin` (contraseña en `data/credenciales_prueba.md`): ve todas
las vistas. Para mostrar lo que ve un cliente, cerrar sesión y entrar con su
`cliente_id` (por ejemplo CLI-000283), o abrir "Vista del cliente" como admin.

Interfaz desde los PRs de UX (base visual, asistente, Mi resumen, login;
nov 2026), verificada el 2026-11-17 sobre esta instancia salvo donde dice
pendiente:

- El login dice que el usuario de un cliente es su número de cliente. Si
  falla, el mensaje es el mismo para usuario o contraseña.
- Arriba de cada vista se lee quién está dentro ("Sesión: Administrador" o
  "Sesión: Cliente · CLI-000283"); en la barra lateral está "Cerrar sesión".
- Tarjetas, tablas y tooltips muestran los montos con centavos y "MXN"
  ($9,796.94 MXN); las frases para el cliente, sin centavos ($27,030). Las
  cifras de este guion van redondeadas como en las frases.

### 1. Portafolio (2 min)

Pregunta que responde: a quien tengo que mirar.

- Cabecera contra la base: ingreso mensual mediano $9,796.94 MXN
  (-$1,675.48 MXN vs. base), 57 % gastan mas de lo que ganan (+37.0 pts).
- La probabilidad media (15.8 %, -1.3 pts) no sube: el modelo se reentrena
  en cada corrida y la etiqueta se calibra a una tasa media de 15 %. La nota
  bajo las metricas lo explica; el deterioro se lee en las otras dos cifras
  y en Observabilidad.
- "Clientes a atender primero", bajo la nota: los 5 de mayor probabilidad
  (CLI-000060 68.1 %, CLI-000059 64.1 %, CLI-000209 62.3 %, CLI-000283
  59.7 %, CLI-000151 57.6 %), cada uno con "Ver cliente".
- Bandas: 255 bajo (51 %), 159 medio (32 %), 86 alto (17 %).
- Tabla "Clientes a revisar", al final, igual que antes: seleccionar una fila
  abre al cliente.
- En el Asistente, toda pregunta del portafolio ("¿Cuántos clientes están
  en riesgo alto?") se hace como `admin`: es el unico rol con
  `resumen_portafolio`; es uno de los botones de ejemplo del admin. Con el
  usuario de un cliente el asistente contesta que no da cifras de todo el
  banco y ofrece compararlo con los demas.

### 2. Cliente (2 min)

Ejemplo de riesgo alto: **CLI-000283**.

- Pastilla "Riesgo alto" y probabilidad de impago 59.7 %. 26 anos, 1.9 anos
  de antiguedad.
- Ingreso $55,535.03 MXN al mes, capacidad de ahorro -$27,030.03 MXN, deuda
  / ingreso 4.63.
- Ultimos 12 meses: neto promedio -$26,816.28 MXN al mes; salidas mayores
  que entradas en 10 de 12 meses.
- A donde se va el dinero: pago de deuda 26 %, suscripciones 15 %,
  servicios 13 %.

Las metricas, el neto y el desglose usan la misma ventana de 12 meses; la
grafica mes a mes muestra los 19 meses de historial.

### 3. Mi resumen, la vista del cliente (2 min)

El mismo **CLI-000283**, ahora como lo ve el cliente al entrar con su
usuario: solo sus datos, sin selector de cliente, en frases cortas y en pesos.

Arriba, la respuesta a "¿cómo voy?":

- Tienes disponible $15,215.70 MXN (saldo al cierre de abr 2027). "A tu
  ritmo de los últimos 12 meses, tu saldo alcanza para menos de 1 mes":
  saldo entre la capacidad de ahorro negativa de 12 meses (-$27,030).
- Tres tarjetas: gastó $106,852.34 MXN este mes (abr 2027), 35 % más que su
  promedio de los 18 meses anteriores; lo que más creció, suscripciones
  ($10,567 más al mes que en los 3 meses anteriores, de $7,107 a $17,674;
  ver [technical-debt.md](technical-debt.md), la cifra no es creíble como
  suscripciones); nivel de riesgo Alto.
- Tu siguiente paso: con riesgo alto, o con déficit y saldo para menos de 3
  meses, primero va eso: "Lo primero: en promedio, en los últimos 12 meses,
  gastas $27,030 más de lo que te entra al mes. A ese ritmo, tu saldo de
  $15,216 alcanza para menos de 1 mes. Además, tu riesgo de atrasarte en tus
  pagos es alto (59.7 %)." Debajo, "Tu paso en la escalera" y el Monte
  Carlo (100 de cada 100 escenarios). Reglas fijas, sin modelo de lenguaje.

Debajo, "El detalle", en el orden de siempre. Cada cifra de déficit dice su
periodo:

- Este mes (abr 2027): le entraron $32,876 y salieron $106,852; la
  diferencia, -$73,976. En promedio, en los 18 meses anteriores, gastaba
  $16,658 más de lo que le entraba al mes.
- Quincenas: le llegan unos $16,438 cada quincena; en may 2026 le llegaban
  unos $34,368 (su ingreso bajó 52 %, el escenario de deriva).
- A dónde se va su dinero (3 meses): pago de deuda 24 %, otros 18 %,
  suscripciones 17 %.
- Su crédito: paso 1 de 6 de la escalera (pagos de deuda bajo control).
  Paga $25,513 al mes de deudas, el 70 % de su ingreso de los últimos 3
  meses ($36,665 al mes); para el siguiente paso deberían quedar en $10,999
  o menos. Usa 38 % de su tarjeta.
- Su riesgo, alto, explicado con sus propios valores SHAP ("Tu riesgo
  estimado de atrasarte en algún pago en los próximos 6 meses: 59.7 %"). Lo
  sube: gasta $27,030 más de lo que le entra al mes (promedio de los
  últimos 12 meses) y debe 4.6 veces su ingreso de un mes. Lo baja: lleva
  1.9 años como cliente.
- ¿Me alcanza para algo de $15,000? La forma de pago se elige arriba del
  formulario; el plazo y la tasa solo aparecen con "A crédito". De contado
  el saldo alcanza ($15,216),
  pero con o sin la compra se queda sin dinero algún mes en 100 de cada 100
  escenarios. A crédito a 12 meses con 45 % anual: $1,631 al mes con IVA
  sobre intereses. Pendiente: volver a calcular las dos en pantalla con el
formulario nuevo (el cálculo no cambió).

La explicación reproduce el score publicado: usa el modelo que lo produjo
(el del pliegue de la validación cruzada). Contraste: **CLI-000379** (riesgo
bajo, 5 %): lo baja sobre todo que le quedan $11,313 al mes, su ingreso
declarado ($40,926 al mes) y su edad (42 años); está en el paso 5 de 6
(fondo de emergencia completo: su saldo cubre 3.4 meses de gastos). Sin
déficit y con riesgo bajo, su siguiente paso queda como antes ("Tu meta
ahora: fondo de emergencia completo...") y la tarjeta de saldo no muestra
autonomía.
CLI-000162, el contraste anterior, quedó en 13 % (banda media) tras #80.

### 4. Simulador de hipoteca (2 min)

Con los parametros por defecto (vivienda de $900,000, tasa de 11.20 % de
`config/mercado_mx.yaml`), dos clientes:

| | CLI-000001 | CLI-000162 |
|---|---|---|
| Riesgo sin hipoteca | 41 % | 4 % |
| Riesgo con hipoteca | 100 % | 25 % |
| Capacidad hipotecaria | No aplica | $407,269 |
| Enganche y gastos ($135,000) | 144 meses (ahorra $687 al mes) | 12 meses |

Para CLI-000001 la capacidad no aplica: ni una vivienda social de $214,047
(el minimo de la Sociedad Hipotecaria Federal) cabe sin subir su riesgo mas
de 5 puntos.

Cuota mensual $8,471 en ambos (con 11.20 %; era $8,361 con 11 %); la
tarjeta la muestra como $8,471.25 MXN. Pendiente: volver a leer la tabla de
arriba en pantalla con el formato nuevo (el cálculo no cambió). Riesgo = escenarios (de 5,000 trayectorias)
en los que el saldo queda negativo en algun mes de los proximos 36. Las
trayectorias remuestrean los meses del propio cliente mas un choque de
perdida de ingreso; todos los escenarios usan los mismos numeros
aleatorios, asi que la diferencia es la hipoteca, no el ruido.

### 5. Observabilidad (3 min)

- Ultima corrida por capa: Bronze, Silver y Gold correctas, 9 corridas
  registradas cada una (la base, la calendarizada que Airflow crea al
  despausar el DAG y las 7 entregas); ninguna fila a cuarentena.
- Duracion por corrida en escala logaritmica: una corrida fuera de lo normal
  se ve aunque las demas tarden segundos.
- Deriva (PSI) contra la referencia congelada de la base. Los paneles usan
  nombres en español: "Probabilidad de impago (score)", "Capacidad de
  ahorro", "Estabilidad del ingreso", "Gasto de los últimos 3 meses",
  "Ingreso mensual". La tabla de abajo conserva los nombres de columna.

| Corte | Score | capacidad_ahorro | estabilidad_ingreso | gasto_promedio_3_meses | ingreso_mensual_promedio |
|---|---|---|---|---|---|
| 2026-10 | 0.082 | 0.025 | 0.005 | 0.006 | 0.002 |
| 2026-12 | 0.169 | 0.074 | 0.099 | 0.061 | 0.011 |
| 2027-01 | 0.341 | 0.126 | 0.257 | 0.086 | 0.034 |
| 2027-02 | 0.363 | 0.246 | 0.359 | 0.129 | 0.072 |
| 2027-04 | 0.540 | 0.668 | 0.371 | 0.166 | 0.077 |

  Umbrales: 0.10 vigilar, 0.25 reentrenar. El score es lo primero que se
  mueve (vigilar desde nov 2026, reentrenar en ene 2027); la estabilidad del
  ingreso cruza a reentrenar en ene 2027 y la capacidad de ahorro en mar
  2027. Al ultimo corte: 3 reentrenar, 1 vigilar, 12 estables, 2 temporales
  (antiguedad y edad derivan por el paso del tiempo y no alertan).

### 6. Asistente sobre la documentacion (1 min)

Como `admin`, en la pagina "Asistente", una pregunta sobre como se
resguarda la clave unica de registro de poblacion. Lo que hay que mostrar no es
la respuesta sino la cita: el asistente busca en la documentación y termina
con la fuente (`archivo § seccion`), no con algo de memoria. La interfaz ya
no muestra el nombre de la herramienta: debajo de la respuesta sale "Según
la documentación del proyecto.".

- Fuente esperada: `docs/seguridad.md § ... > Clasificacion de datos por
  capa` (los tres fragmentos que recibe el modelo salen de ahi y del
  diccionario de datos).
- Respaldo si la primera no cita bien: el origen de la informacion con
  la que se construyo el gemelo; fuente esperada `docs/seguridad.md § ... > Datos
  personales sinteticos`.
- **Pendiente de probar en la Mac** con `llama3.1:8b` antes de grabar el
  clip: que elija la herramienta y cite la fuente, y que aparezca la línea
  "Según la documentación del proyecto.".

Botones de ejemplo del Asistente (las ocho se corrieron sobre esta instancia
el 2026-11-17, ver [evaluacion-asistente.md](evaluacion-asistente.md)):
cliente, en qué categoría gasta más, qué tan probable es que se atrase,
qué paso le toca en su escalera y si ahorra más o menos que los demás;
admin, clientes en riesgo alto, porcentaje que gasta más de lo que gana
(responde 56.6 %; la tarjeta de Portafolio lo redondea a 57 %), ingreso
mediano y probabilidad media. Con el usuario de un cliente, el asistente le habla de
tú ("Gastas más en pagos de deuda...").

Las preguntas se describen aqui sin su redaccion literal a proposito: este
archivo tambien esta indexado, y una pregunta escrita igual que en la demo
haria que el buscador devolviera este guion en lugar de la seccion que la
responde.

## Preguntas probables

- **Que tan bueno es el modelo?** AUC 0.690 +- 0.052 en validacion cruzada
  (5 x 5) sobre la base, contra 0.851 del oraculo (la probabilidad verdadera,
  que conoce el rasgo oculto y el choque). Sin las 3 variables de la
  etiqueta baja a 0.638. `experimentos/auc_evento_posterior.py`, medido el
  2026-11-13 despues de la correccion de coherencia temporal (#80); antes
  daba 0.692, asi que la correccion no movio el modelo. Con 500 clientes el
  AUC depende mucho de la muestra: un solo split de esta misma base da
  0.623. Con 5,000 clientes es 0.777 +- 0.017 (medido el 2026-11-04, antes
  de #80).
- **La probabilidad es una probabilidad?** Si: esta calibrada. En la
  instancia, la media publicada es 15.8 % y la tasa real de impago 15.6 %;
  ningun cliente pasa de 90 %. Con 500 clientes el modelo distingue poco,
  asi que una probabilidad honesta se queda cerca de la media (de 1 % a
  68 %) y la banda media junta a 159 clientes. Ordena igual que antes (AUC 0.777 contra 0.776).
  `experimentos/calibracion_riesgo.py` y
  [technical-debt.md](technical-debt.md).
- **Por que el modelo y el simulador dan cifras distintas para el mismo
  cliente?** Miden cosas distintas: el modelo ordena el riesgo de atrasarse
  en pagos con todo el historial, el Monte Carlo remuestrea su flujo.
  CLI-000001 tiene riesgo medio (19.8 %); en el simulador, sin hipoteca, se
  queda sin dinero algun mes en 41 de cada 100 escenarios a 36 meses y en 7
  de cada 100 a 6 meses, el horizonte del modelo.
- **Por que no sube la probabilidad media?** Ver Portafolio: calibracion a
  tasa fija en cada reentrenamiento.
- **Los datos son reales?** No: sinteticos, reproducibles con la semilla 42 y
  la fecha de referencia; los parametros del escenario de deriva estan
  calibrados para que el detector tenga algo que detectar en 7 entregas.
- **Que pasa con la PII?** Se protege al escribir Silver (hash con sal y
  enmascarado); Gold y el dashboard no tienen PII. [seguridad.md](seguridad.md).

# Guion de la demo

Como reconstruir en cualquier maquina la instancia que se usa para presentar
y un recorrido de unos 11 minutos por el dashboard sobre ella. La instancia
es la sesion base al 30 sep 2026 (500 clientes) mas 7 entregas mensuales con
el escenario de deriva activo; el ultimo corte queda al 30 abr 2027.

Requisitos e instalacion: [ejecucion-local.md](ejecucion-local.md).

## Reconstruir la instancia

Los datos salen de la configuracion versionada (semilla 42, fecha de
referencia, 500 clientes), asi que la misma secuencia produce la misma
instancia en otra maquina. Unos 15 minutos con la imagen del worker ya
construida.

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

   Opcional, para confirmar el modelo de la base (unos minutos):

   ```bash
   docker compose run --rm worker experimentos/auc_evento_posterior.py
   ```

   Debe reportar 81 positivos y AUC por pliegue 0.689.

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

Entrar como `admin` (contraseña en `data/credenciales_prueba.md`): ve todas
las vistas. Para mostrar lo que ve un cliente, cerrar sesión y entrar con su
`cliente_id` (por ejemplo CLI-000283), o abrir "Vista del cliente" como admin.

### 1. Portafolio (2 min)

Pregunta que responde: a quien tengo que mirar.

- Cabecera contra la base: ingreso mensual mediano $9,797 (-$1,675), 58 %
  gastan mas de lo que ganan (+34.2 pts).
- La probabilidad media (18.7 %, -1.4 pts) no sube: el modelo se reentrena
  en cada corrida y la etiqueta se calibra a una tasa media de 15 %. La nota
  bajo las metricas lo explica; el deterioro se lee en las otras dos cifras
  y en Observabilidad.
- Bandas: 305 bajo (61 %), 83 medio (17 %), 112 alto (22 %).
- Tabla "Clientes a revisar": seleccionar una fila abre al cliente.

### 2. Cliente (2 min)

Ejemplo de riesgo alto: **CLI-000283**.

- Probabilidad de impago 95.3 %. 26 anos, 1.2 anos de antiguedad.
- Ingreso $55,535 al mes, capacidad de ahorro -$27,030, deuda / ingreso 5.50.
- Ultimos 12 meses: neto promedio -$26,816 al mes; salidas mayores que
  entradas en 10 de 12 meses.
- A donde se va el dinero: pago de deuda 26 %, suscripciones 15 %,
  servicios 13 %.

Las metricas, el neto y el desglose usan la misma ventana de 12 meses; la
grafica mes a mes muestra los 19 meses de historial.

### 3. Mi resumen, la vista del cliente (2 min)

El mismo **CLI-000283**, ahora como lo ve el cliente al entrar con su
usuario: solo sus datos, sin selector de cliente, en frases cortas y en pesos.

- Este mes (abr 2027): le entraron $32,876 y salieron $106,852. En un mes
  normal gasta $16,658 más de lo que le entra.
- Quincenas: le llegan unos $16,438 cada quincena; en may 2026 le llegaban
  unos $34,368 (su ingreso bajó 52 %, el escenario de deriva).
- A dónde se va su dinero (3 meses): pago de deuda 24 %, otros 18 %,
  suscripciones 17 %.
- Su crédito: paso 1 de 6 de la escalera, pagos de deuda de $25,513 al mes,
  46 % de su ingreso promedio; usa 38 % de su tarjeta.
- Su riesgo, alto, explicado con sus propios valores SHAP. Lo sube: debe 5.5
  veces su ingreso de un mes, gasta $27,030 más de lo que le entra al mes y
  gasta en compras unos $60,433 al mes.
- ¿Me alcanza para algo de $15,000? De contado el saldo alcanza ($15,216),
  pero con o sin la compra se queda sin dinero algún mes en 100 de cada 100
  escenarios. A crédito a 12 meses con 45 % anual: $1,631 al mes con IVA
  sobre intereses.

La explicación reproduce el score publicado: usa el modelo que lo produjo
(el del pliegue de la validación cruzada). Contraste: **CLI-000162** (riesgo
bajo, 5 %) lo baja sobre todo que le quedan $10,730 al mes; está en el paso 3
(fondo de emergencia: su saldo cubre 1.1 meses de gastos).

### 4. Simulador de hipoteca (2 min)

Con los parametros por defecto (vivienda de $900,000), dos clientes:

| | CLI-000001 | CLI-000162 |
|---|---|---|
| Riesgo sin hipoteca | 89 % | 4 % |
| Riesgo con hipoteca | 100 % | 25 % |
| Capacidad hipotecaria | $48,828 | $410,156 |
| Enganche y gastos ($135,000) | No ahorra, no los reuniria | 12 meses |

Cuota mensual $8,361 en ambos. Riesgo = escenarios (de 5,000 trayectorias)
en los que el saldo queda negativo en algun mes de los proximos 36. Las
trayectorias remuestrean los meses del propio cliente mas un choque de
perdida de ingreso; todos los escenarios usan los mismos numeros
aleatorios, asi que la diferencia es la hipoteca, no el ruido.

### 5. Observabilidad (3 min)

- Ultima corrida por capa: Bronze, Silver y Gold correctas, 8 corridas
  registradas cada una; ninguna fila a cuarentena.
- Duracion por corrida en escala logaritmica: una corrida fuera de lo normal
  se ve aunque las demas tarden segundos.
- Deriva (PSI) contra la referencia congelada de la base:

| Corte | Score | capacidad_ahorro | estabilidad_ingreso | gasto_promedio_3_meses | ingreso_mensual_promedio |
|---|---|---|---|---|---|
| 2026-10 | 0.083 | 0.007 | 0.005 | 0.006 | 0.002 |
| 2026-12 | 0.215 | 0.061 | 0.099 | 0.061 | 0.011 |
| 2027-01 | 0.354 | 0.134 | 0.257 | 0.086 | 0.034 |
| 2027-02 | 0.411 | 0.221 | 0.359 | 0.129 | 0.072 |
| 2027-04 | 0.626 | 0.591 | 0.371 | 0.166 | 0.077 |

  Umbrales: 0.10 vigilar, 0.25 reentrenar. El score es lo primero que se
  mueve (vigilar desde nov 2026, reentrenar en ene 2027); la estabilidad del
  ingreso cruza a reentrenar en ene 2027 y la capacidad de ahorro en mar
  2027. Al ultimo corte: 3 reentrenar, 1 vigilar, 12 estables, 2 temporales
  (antiguedad y edad derivan por el paso del tiempo y no alertan).

## Preguntas probables

- **Que tan bueno es el modelo?** AUC 0.689 +- 0.071 en validacion cruzada
  (5 x 5) sobre la base, contra 0.855 del oraculo (la probabilidad verdadera,
  que conoce el rasgo oculto y el choque). Sin las 3 variables de la
  etiqueta baja a 0.667. `experimentos/auc_evento_posterior.py`. Con 500
  clientes el AUC depende mucho de la muestra: con los ingresos calibrados a
  Mexico bajo de 0.741 a 0.689, mientras que con 5,000 clientes no cambio
  (0.804 contra 0.803).
- **Por que un cliente puede salir "bajo" y aun asi quedarse sin dinero en
  el simulador?** Miden cosas distintas: el modelo ordena el riesgo de
  atrasarse en pagos con todo el historial, el Monte Carlo remuestrea su
  flujo. CLI-000001 tiene riesgo bajo (9 %) y aun asi en 89 de cada 100
  escenarios se queda sin dinero algun mes; su ingreso bajo 52 % en el ano.
- **Por que no sube la probabilidad media?** Ver Portafolio: calibracion a
  tasa fija en cada reentrenamiento.
- **Los datos son reales?** No: sinteticos, reproducibles con la semilla 42 y
  la fecha de referencia; los parametros del escenario de deriva estan
  calibrados para que el detector tenga algo que detectar en 7 entregas.
- **Que pasa con la PII?** Se protege al escribir Silver (hash con sal y
  enmascarado); Gold y el dashboard no tienen PII. [seguridad.md](seguridad.md).

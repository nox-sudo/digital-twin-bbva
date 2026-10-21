# Guion de la demo

Como reconstruir en cualquier maquina la instancia que se usa para presentar
y un recorrido de unos 10 minutos por el dashboard sobre ella. La instancia
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

   Debe reportar 82 positivos y AUC por pliegue 0.741.

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

6. **Abrir el dashboard:**

   ```bash
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

### 1. Portafolio (2 min)

Pregunta que responde: a quien tengo que mirar.

- Cabecera contra la base: ingreso mensual mediano $13,759 (-$2,002), 57 %
  gastan mas de lo que ganan (+33.8 pts).
- La probabilidad media (18.3 %, -2.2 pts) no sube: el modelo se reentrena
  en cada corrida y la etiqueta se calibra a una tasa media de 15 %. La nota
  bajo las metricas lo explica; el deterioro se lee en las otras dos cifras
  y en Observabilidad.
- Bandas: 309 bajo (62 %), 72 medio (14 %), 119 alto (24 %).
- Tabla "Clientes a revisar": seleccionar una fila abre al cliente.

### 2. Cliente (2 min)

Ejemplo de riesgo alto: **CLI-000283**.

- Probabilidad de impago 96.8 %. 26 anos, 1.2 anos de antiguedad.
- Ingreso $30,387 al mes, capacidad de ahorro -$14,792, deuda / ingreso 5.50.
- Ultimos 12 meses: neto promedio -$14,578 al mes; salidas mayores que
  entradas en 10 de 12 meses.
- A donde se va el dinero: pago de deuda 26 %, suscripciones 15 %,
  servicios 13 %.

Las metricas, el neto y el desglose usan la misma ventana de 12 meses; la
grafica mes a mes muestra los 19 meses de historial.

### 3. Simulador de hipoteca (2 min)

Con los parametros por defecto (vivienda de $900,000), dos clientes:

| | CLI-000001 | CLI-000162 |
|---|---|---|
| Riesgo sin hipoteca | 88 % | 4 % |
| Riesgo con hipoteca | 100 % | 18 % |
| Capacidad hipotecaria | $39,062 | $498,047 |
| Enganche y gastos ($135,000) | No ahorra, no los reuniria | 10 meses |

Cuota mensual $8,361 en ambos. Riesgo = escenarios (de 5,000 trayectorias)
en los que el saldo queda negativo en algun mes de los proximos 36. Las
trayectorias remuestrean los meses del propio cliente mas un choque de
perdida de ingreso; todos los escenarios usan los mismos numeros
aleatorios, asi que la diferencia es la hipoteca, no el ruido.

### 4. Observabilidad (3 min)

- Ultima corrida por capa: Bronze, Silver y Gold correctas, 8 corridas
  registradas cada una; ninguna fila a cuarentena.
- Duracion por corrida en escala logaritmica: una corrida fuera de lo normal
  se ve aunque las demas tarden segundos.
- Deriva (PSI) contra la referencia congelada de la base:

| Corte | Score | capacidad_ahorro | estabilidad_ingreso | gasto_promedio_3_meses | ingreso_mensual_promedio |
|---|---|---|---|---|---|
| 2026-10 | 0.117 | 0.011 | 0.002 | 0.005 | 0.005 |
| 2026-12 | 0.263 | 0.082 | 0.037 | 0.090 | 0.010 |
| 2027-01 | 0.358 | 0.133 | 0.102 | 0.137 | 0.025 |
| 2027-02 | 0.390 | 0.241 | 0.155 | 0.197 | 0.055 |
| 2027-04 | 0.574 | 0.654 | 0.249 | 0.245 | 0.113 |

  Umbrales: 0.10 vigilar, 0.25 reentrenar. El score es lo primero que se
  mueve (vigilar desde la primera entrega, reentrenar en dic 2026);
  capacidad de ahorro cruza a reentrenar en mar 2027. Al ultimo corte: 2
  reentrenar, 3 vigilar, 11 estables, 2 temporales (antiguedad y edad
  derivan por el paso del tiempo y no alertan).

## Preguntas probables

- **Que tan bueno es el modelo?** AUC 0.741 +- 0.040 en validacion cruzada
  (5 x 5) sobre la base, contra 0.864 del oraculo (la probabilidad verdadera,
  que conoce el rasgo oculto y el choque). Sin las 3 variables de la
  etiqueta baja a 0.681. `experimentos/auc_evento_posterior.py`.
- **Por que no sube la probabilidad media?** Ver Portafolio: calibracion a
  tasa fija en cada reentrenamiento.
- **Los datos son reales?** No: sinteticos, reproducibles con la semilla 42 y
  la fecha de referencia; los parametros del escenario de deriva estan
  calibrados para que el detector tenga algo que detectar en 7 entregas.
- **Que pasa con la PII?** Se protege al escribir Silver (hash con sal y
  enmascarado); Gold y el dashboard no tienen PII. [seguridad.md](seguridad.md).

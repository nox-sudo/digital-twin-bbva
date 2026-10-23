# Monitoreo de deriva de datos (PSI)

Que mide el paso `monitorear_deriva` del DAG (`monitor_drift.py`), por que
la referencia es fija y que queda fuera de alcance. Configuracion en
`config/monitoreo_psi.yaml`; calculo en `src/monitoreo/`.

## Que mide el PSI

El indice de estabilidad poblacional compara la distribucion actual de una
variable con su distribucion de referencia, intervalo por intervalo:

    PSI = suma de (actual - referencia) * ln(actual / referencia)

Vale 0 si ambas distribuciones son iguales y crece a medida que la masa se
mueve de un intervalo a otro. Se calcula para cada feature numerica del
modelo (todas menos las columnas one-hot de la categoria de gasto) y para
el score, la probabilidad de impago que el modelo base asigna a la
poblacion actual.

| PSI | Estado | Lectura |
|---|---|---|
| < 0.10 | estable | Sin cambio relevante |
| 0.10 a 0.25 | vigilar | Cambio moderado: revisar la variable |
| > 0.25 | reentrenar | La poblacion ya no se parece a la de entrenamiento |
| cualquiera | temporal | Variable que deriva por el paso del tiempo (`variables_temporales`); se reporta sin alerta |

Los umbrales son los de uso comun en riesgo de credito, no se derivaron de
estos datos, y se cambian en el YAML. Un intervalo vacio toma la proporcion
`epsilon` (0.0001) para que `ln(0)` no rompa el calculo; si esta vacio en
ambas distribuciones, aporta 0. Los valores faltantes tienen su propio
intervalo, para que un aumento de nulos tambien cuente como deriva.

Con 500 clientes el PSI tiene ruido de muestreo: dos muestras de la misma
distribucion dan, en promedio, alrededor de `(intervalos - 1) * (1/n_ref +
1/n_actual)`, unos 0.04 con 10 intervalos. Valores por debajo de eso no
indican nada.

## Por que los intervalos son fijos

Los cortes son los deciles de la distribucion de entrenamiento y se
calculan una sola vez, al crear la referencia. Cada corrida solo cuenta en
que intervalo cae cada valor nuevo. Si los deciles se recalcularan con los
datos nuevos, cada intervalo volveria a tener ~10 % por construccion y un
desplazamiento de toda la distribucion (todos los ingresos 30 % mas bajos,
por ejemplo) daria PSI cercano a 0. La prueba
`test_las_corridas_siguientes_reutilizan_los_cortes_de_entrenamiento`
muestra los dos calculos sobre los mismos datos.

## Linea base congelada

El DAG reentrena el modelo en cada corrida. Si la referencia se renovara
con cada modelo, el PSI compararia cada mes solo con el anterior, y una
deriva lenta (por ejemplo, 2 % menos de ingreso cada mes) nunca cruzaria
un umbral aunque al cabo de un ano la poblacion fuera otra. Por eso:

- La primera corrida crea la referencia con el modelo recien entrenado y
  su poblacion de entrenamiento (los clientes con etiqueta). Esa corrida da
  PSI 0 y es el punto de partida de la tendencia.
- Las corridas siguientes no la tocan. El score se calcula con la copia
  del **modelo base** guardada en la referencia, no con el modelo
  reentrenado: asi el PSI del score refleja solo el cambio en los datos.
- `python main.py monitor --nueva-referencia` promueve una linea base
  nueva. La anterior queda archivada en su carpeta con fecha; nunca se
  sobrescribe ni se borra. `activa.json` marca cual esta activa y cual
  habia antes.

```
models/referencia_psi/
    activa.json
    ref_20261016_130501/
        referencia.json       cortes y proporciones de entrenamiento (sin datos de clientes)
        modelo_base.joblib    copia del modelo; su SHA-256 esta en referencia.json
```

`demo.sh respaldar` incluye `models/referencia_psi/` con el hash de cada
archivo en `MANIFIESTO.sha256`. `demo.sh limpiar` la conserva; solo
`limpiar todo` la borra.

## Resultado

Cada corrida agrega un Parquet a `data/calidad/deriva_psi/`, junto al
registro de calidad, con esquema fijo: `run_id`, `fecha_corrida`,
`fecha_corte`, `referencia_id`, `variable`, `tipo` (feature o score),
`psi`, `estado`, `n_referencia`, `n_actual`. Se lee sin Spark:

```sql
SELECT fecha_corte, variable, psi, estado
FROM read_parquet('data/calidad/deriva_psi/*.parquet')
ORDER BY fecha_corrida, psi DESC;
```

El dashboard lo muestra en Observabilidad, seccion "Deriva de datos (PSI)":
el conteo por estado del ultimo corte, un panel por variable con la
tendencia contra la referencia activa y las bandas de 0.10 y 0.25, y la
tabla de la ultima corrida. Si un corte se midio varias veces, toma la
ultima corrida; las corridas contra referencias anteriores no se mezclan.

Una variable en estado `reentrenar` se registra en el log de la tarea como
`ALERTA deriva`, y el resumen de la corrida queda en
`data/logs/monitoreo_run_*.json`. El pipeline no falla por deriva.

## Data drift y concept drift

- **Data drift** (lo que se mide): cambia la distribucion de las entradas,
  P(X). Se detecta sin esperar a saber que paso con los clientes, porque
  solo usa las features y el score.
- **Concept drift** (fuera de alcance): cambia la relacion entre las
  entradas y el resultado, P(impago | X). La misma razon deuda/ingreso
  podria implicar mas riesgo en una recesion. Medirlo exige etiquetas
  reales del periodo actual (si el cliente cayo en impago dentro del
  horizonte de 6 meses), que solo existen meses despues de la prediccion.
  Aqui la etiqueta sale de un modelo latente con parametros fijos
  (`config/etiquetas_impago.yaml`), asi que la relacion no cambia por
  construccion.

## Limitaciones conocidas

**Dilucion por promediar.** Hasta el 2026-10-19 las features de flujo se
promediaban sobre todo el historial, y un cambio en los ultimos meses apenas
movia el promedio: el escenario de deriva lo mostro con el pipeline (seccion
"Medicion con el pipeline"). Desde entonces se calculan sobre una ventana de
12 meses (`config/features.yaml`; seccion "Ventana de 12 meses"). La ventana
tambien diluye, pero mucho menos: un deterioro que lleva 7 de los 12 meses
pesa casi 60 % del promedio, no 7 de 19.

**La alerta es informativa.** El DAG reentrena cada corrida sin
condiciones, asi que una alerta `reentrenar` no dispara nada que no fuera a
pasar de todos modos: sirve para saber que la poblacion cambio y desde
cuando. Reentrenar solo cuando el PSI lo indique (y promover la nueva
referencia en ese momento) queda como trabajo futuro.

## Escenario de deriva para probar el detector

Sin escenario, el generador agrega cada mes con los mismos perfiles por
cliente y el PSI se queda cerca de 0 aunque la instancia crezca. El
escenario (`escenario_deriva` en `config/sesion.yaml`,
`src/common/escenario_deriva.py`) deteriora a una parte de la cartera a
partir de cierta entrega: el ingreso cae y el gasto sube de forma compuesta
cada mes, y baja la probabilidad de pagar cada deuda.

Para usarlo en una instancia larga:

1. Poner `activo: true` en `config/sesion.yaml` antes de la primera entrega
   afectada (`desde_entrega`).
2. Correr `bash demo.sh nueva-entrega` las veces que se quiera. Cada entrega
   genera una sesion con su propio id (sufijo `_d<huella>` del escenario) y
   el monitoreo mide contra la referencia congelada de la sesion base.

Garantias:

- **Apagado no cambia nada.** Con `activo: false` la sesion sale byte a byte
  igual que antes de existir el escenario (mismo id, mismos parametros,
  mismos checksums).
- **Meses base intactos.** El escenario solo toca meses posteriores al corte
  base; clientes, cuentas, CETES y los meses base no cambian, asi que la
  referencia del PSI sigue siendo valida.
- **Reproducible.** Los clientes afectados se sortean con un generador
  propio derivado de la semilla de la sesion, y los efectos no hacen sorteos
  nuevos: fechas, categorias y numero de compras de un mes son los mismos
  con o sin escenario. Un pago suprimido deja un hueco en la numeracion de
  `transaccion_id` en vez de recorrer los ids siguientes.
- **No se puede cambiar despues de entregar.** Si la sesion nueva continua
  a la activa (misma semilla, fecha base, clientes y meses; el escenario no
  cuenta) y cambiaria algun archivo ya entregado, el generador se detiene
  sin activar nada. Activar el escenario con un `desde_entrega` posterior a
  lo ya entregado si esta permitido.

**Los valores de ejemplo estan calibrados, no son realistas.** Se probaron
varias configuraciones y se dejo la que lleva el PSI a "vigilar" en unas 6
entregas: 60 % de clientes afectados, ingreso -10 % y gasto +8 % compuestos
por mes. El criterio fue que el detector tenga algo que detectar en el
horizonte de una demo, no representar una crisis plausible. Es lo contrario
de las etiquetas de impago, cuyos parametros se fijaron antes de ver
resultados.

**Calibracion (aproximacion).** Para elegir los valores de ejemplo se
probaron configuraciones calculando el PSI sobre los CSV generados, con la
misma definicion que los KPIs (nomina y depositos sobre la rejilla completa
cliente x mes; compras de los ultimos 3 meses) y los cortes por deciles de la
sesion base, sin correr el pipeline. Sesion base s42, fecha 2026-09-30, 500
clientes, 12 meses, `desde_entrega: 1`:

| Escenario (afectados / ingreso / gasto por mes) | Entregas | Ingreso promedio, todo el historial | Gasto, ultimos 3 meses |
|---|---|---|---|
| 40 % / -5 % / +3 % | 6 | 0.012 estable | 0.033 estable |
| 40 % / -5 % / +3 % | 12 | 0.037 estable | 0.081 estable |
| 60 % / -10 % / +8 % (ejemplo) | 6 | 0.036 estable | 0.186 vigilar |
| 100 % / -15 % / +10 % | 3 | 0.021 estable | 0.239 vigilar |
| 100 % / -15 % / +10 % | 6 | 0.113 vigilar | 0.780 reentrenar |

Estas cifras son una aproximacion y sirven solo para la calibracion; las que
se pueden citar son las del pipeline (abajo). Para el escenario de ejemplo
coinciden: la aproximacion dio 0.036 y 0.186 y el pipeline, 0.036 y 0.186.

## Medicion con el pipeline (2026-10-18)

Medida con las features sobre todo el historial, antes de la ventana de 12
meses; la medicion vigente esta en la seccion "Ventana de 12 meses".

Instancia regenerada desde cero con la sesion base s42, fecha 2026-09-30, 500
clientes, 12 meses (referencia `ref_20261018_203222`). Escenario de ejemplo
activo (60 % de clientes, ingreso -10 %, gasto +8 %, puntualidad -5 puntos
por mes, `desde_entrega: 1`) y 6 corridas de `bash demo.sh nueva-entrega`;
cada una corre el DAG completo y el paso `monitorear_deriva` agrega una
corrida al historico. PSI por fecha de corte (`data/calidad/deriva_psi/`):

| Variable | 2026-10 | 2026-11 | 2026-12 | 2027-01 | 2027-02 | 2027-03 | Estado final |
|---|---|---|---|---|---|---|---|
| `antiguedad_dias` | 0.032 | 0.115 | 0.334 | 0.828 | 0.860 | 1.007 | reentrenar |
| `proporcion_retiros` | 0.093 | 0.363 | 0.577 | 0.712 | 0.538 | 0.556 | reentrenar |
| `probabilidad_impago` (score) | 0.068 | 0.164 | 0.236 | 0.263 | 0.348 | 0.351 | reentrenar |
| `capacidad_ahorro` | 0.012 | 0.038 | 0.058 | 0.095 | 0.144 | 0.223 | vigilar |
| `gasto_promedio_3_meses` | 0.005 | 0.030 | 0.090 | 0.137 | 0.197 | 0.186 | vigilar |
| `estabilidad_ingreso` | 0.003 | 0.002 | 0.015 | 0.054 | 0.116 | 0.145 | vigilar |
| `actividad_p2p` | 0.018 | 0.023 | 0.034 | 0.040 | 0.052 | 0.092 | estable |
| `ingreso_mensual_promedio` | 0.003 | 0.009 | 0.007 | 0.013 | 0.026 | 0.036 | estable |
| `gasto_mensual_total` | 0.001 | 0.005 | 0.011 | 0.010 | 0.013 | 0.023 | estable |

Las variables que salen de entidades que no cambian entre entregas
(`saldo_liquido_disponible`, `uso_linea_credito`, `ratio_endeudamiento`,
productos, ingreso declarado) quedan en 0 o casi 0, como se espera.

Lo que muestra la tabla:

- **El escenario se ve en las variables de flujo reciente.** Capacidad de
  ahorro, gasto de los ultimos 3 meses y estabilidad del ingreso llegan a
  "vigilar" entre la entrega 4 y la 6.
- **El ingreso promedio no reacciona.** Con 60 % de clientes perdiendo 10 %
  de ingreso compuesto cada mes, el PSI del ingreso promedio llega a 0.036
  tras 6 entregas: la dilucion descrita en Limitaciones, confirmada con el
  pipeline.
- **Dos de las tres alertas "reentrenar" no vienen del escenario.** Ver la
  seccion siguiente.

## Deriva que viene del paso del tiempo

Medido el 2026-10-18, con las features sobre todo el historial.
`antiguedad_dias` y `proporcion_retiros` derivaban aunque la poblacion no
cambiara:

- **`antiguedad_dias`** se mide contra la fecha de corte, que avanza un mes
  en cada entrega: todos los clientes suman unos 30 dias por entrega. Contra
  deciles congelados, la distribucion entera se desplaza y el PSI crece por
  construccion (1.007 tras 6 entregas). `edad_anios` tiene el mismo origen,
  pero en anios el desplazamiento es pequeno (0.003).
- **`proporcion_retiros`** (retiros / transacciones de todo el historial) se
  concentra al acumular meses: en la sesion base 42 clientes tenian cero
  retiros y tras 6 entregas sin escenario quedan 12. Control: con los mismos
  CSV y los cortes de la base, el PSI de esta variable da 0.556 con y sin
  escenario, el mismo valor que midio el pipeline.

El PSI del score (0.351) mezcla las dos fuentes: el modelo base recibe tanto
las variables que movio el escenario como las que se movieron con el tiempo,
y sin una corrida de control del pipeline no se puede separar cuanto aporta
cada una.

En una cartera real pasaria lo mismo: una referencia congelada contra
variables que dependen de la fecha de corte alerta aunque no haya cambiado
nada.

**Tratamiento.** La proporcion de retiros dejo de derivar al pasar a la
ventana de 12 meses (seccion siguiente) y salio de la lista. `antiguedad_dias`
y `edad_anios` miden el tiempo por definicion y siguen en
`variables_temporales` (`config/monitoreo_psi.yaml`). Su PSI se sigue
calculando y guardando en el historico, pero con estado `temporal`: no se
compara con los umbrales ni genera alerta en el log. Una variable listada que
no exista en la referencia produce un aviso, para que un error en el nombre
no la deje fuera sin que se note. El score nunca es temporal. La tabla de
arriba se midio antes de este cambio; con el, esas dos variables aparecen
como `temporal` y quedan una alerta `reentrenar` (el score) y tres `vigilar`.

Para antiguedad y edad es una mitigacion: oculta la alerta falsa, pero esas
variables siguen cambiando con el corte (`docs/technical-debt.md`, "Features
que dependen del tiempo derivan sin cambio de poblacion").

## Ventana de 12 meses

Desde el 2026-10-19 los KPIs de flujo (ingreso, estabilidad, gasto, capacidad
de ahorro, ratio de endeudamiento por su ingreso, frecuencia, P2P y categoria
dominante) y la proporcion de retiros se calculan sobre los ultimos 12 meses
completos hasta la fecha de corte (`config/features.yaml`). 12 son los meses
de la sesion base: con ese historial la ventana es el historial completo, asi
que las features de la base, el modelo entrenado sobre ellas, el AUC (0.741)
y la referencia del PSI no cambian. Lo que cambia es la lectura de las
entregas.

Efecto medido antes de implementarlo, con las 8 sesiones de la instancia
(base y entregas 1 a 7, escenario de ejemplo) y las definiciones de los KPIs
calculadas sobre los CSV; PSI contra los deciles de la base. Es una
**aproximacion**: para las features de historial completo coincide con lo
que midio el pipeline (capacidad de ahorro 0.311, estabilidad 0.177 y retiros
0.930 en la entrega 7).

| Variable | Definicion | e1 | e3 | e5 | e7 |
|---|---|---|---|---|---|
| `capacidad_ahorro` | historial completo | 0.012 | 0.058 | 0.144 | 0.311 |
| | ventana de 12 meses | 0.011 | 0.082 | 0.241 | 0.654 |
| `ingreso_mensual_promedio` | historial completo | 0.003 | 0.007 | 0.026 | 0.049 |
| | ventana de 12 meses | 0.005 | 0.010 | 0.055 | 0.113 |
| `estabilidad_ingreso` | historial completo | 0.003 | 0.015 | 0.116 | 0.177 |
| | ventana de 12 meses | 0.002 | 0.037 | 0.155 | 0.249 |
| `proporcion_retiros` | historial completo | 0.093 | 0.577 | 0.538 | 0.930 |
| | ventana de 12 meses | 0.009 | 0.037 | 0.040 | 0.047 |

La deriva real del escenario se ve entre 1.5 y 2 veces mas fuerte y la falsa
de los retiros desaparece.

**Medicion con el pipeline (2026-10-20).** Instancia reconstruida desde cero
con la ventana: sesion base s42, fecha 2026-09-30, 500 clientes, 12 meses
(referencia `ref_20261019_221201`; AUC 0.741 +- 0.040 y 82 positivos, igual
que antes), escenario de ejemplo activo y 7 corridas de
`bash demo.sh nueva-entrega`. PSI por fecha de corte
(`data/calidad/deriva_psi/`):

| Variable | 2026-10 | 2026-11 | 2026-12 | 2027-01 | 2027-02 | 2027-03 | 2027-04 | Estado final |
|---|---|---|---|---|---|---|---|---|
| `capacidad_ahorro` | 0.011 | 0.043 | 0.082 | 0.133 | 0.241 | 0.404 | 0.654 | reentrenar |
| `probabilidad_impago` (score) | 0.117 | 0.210 | 0.263 | 0.358 | 0.390 | 0.495 | 0.574 | reentrenar |
| `estabilidad_ingreso` | 0.002 | 0.007 | 0.037 | 0.102 | 0.155 | 0.183 | 0.249 | vigilar |
| `gasto_promedio_3_meses` | 0.005 | 0.030 | 0.090 | 0.137 | 0.197 | 0.186 | 0.245 | vigilar |
| `ingreso_mensual_promedio` | 0.005 | 0.011 | 0.010 | 0.025 | 0.055 | 0.075 | 0.113 | vigilar |
| `gasto_mensual_total` | 0.003 | 0.007 | 0.015 | 0.012 | 0.023 | 0.052 | 0.065 | estable |
| `proporcion_retiros` | 0.009 | 0.030 | 0.037 | 0.026 | 0.040 | 0.058 | 0.047 | estable |
| `actividad_p2p` | 0.016 | 0.007 | 0.013 | 0.021 | 0.020 | 0.041 | 0.014 | estable |
| `antiguedad_dias` | 0.032 | 0.115 | 0.334 | 0.828 | 0.860 | 1.007 | 1.658 | temporal |

En el ultimo corte: 2 reentrenar, 3 vigilar, 11 estables y 2 temporales, y
todas las alertas corresponden al escenario. Las cifras coinciden con la
aproximacion sobre CSV de la tabla anterior. `actividad_p2p`, que con todo el
historial llegaba a "vigilar" en la entrega 7 (0.118), queda estable: tambien
derivaba por acumular historial, no por el escenario (que no toca el P2P).

**Medicion con los ingresos calibrados a Mexico (2026-10-23).** Misma
secuencia, con la sesion base regenerada con `config/ingresos.yaml`
(referencia `ref_20261023_180316`; AUC 0.689 +- 0.071 y 81 positivos):

| Variable | 2026-10 | 2026-11 | 2026-12 | 2027-01 | 2027-02 | 2027-03 | 2027-04 | Estado final |
|---|---|---|---|---|---|---|---|---|
| `capacidad_ahorro` | 0.007 | 0.037 | 0.061 | 0.134 | 0.221 | 0.370 | 0.591 | reentrenar |
| `probabilidad_impago` (score) | 0.083 | 0.151 | 0.215 | 0.354 | 0.411 | 0.468 | 0.626 | reentrenar |
| `estabilidad_ingreso` | 0.005 | 0.034 | 0.099 | 0.257 | 0.359 | 0.342 | 0.371 | reentrenar |
| `gasto_promedio_3_meses` | 0.006 | 0.024 | 0.061 | 0.086 | 0.129 | 0.122 | 0.166 | vigilar |
| `ingreso_mensual_promedio` | 0.002 | 0.005 | 0.011 | 0.034 | 0.072 | 0.060 | 0.077 | estable |
| `gasto_mensual_total` | 0.002 | 0.003 | 0.004 | 0.009 | 0.016 | 0.025 | 0.038 | estable |
| `proporcion_retiros` | 0.009 | 0.030 | 0.037 | 0.026 | 0.040 | 0.058 | 0.047 | estable |
| `actividad_p2p` | 0.016 | 0.007 | 0.013 | 0.021 | 0.020 | 0.041 | 0.014 | estable |
| `antiguedad_dias` | 0.032 | 0.115 | 0.334 | 0.828 | 0.860 | 1.007 | 1.658 | temporal |

En el ultimo corte: 3 reentrenar, 1 vigilar, 12 estables y 2 temporales. Con
ingresos con colas mas anchas la estabilidad del ingreso deriva antes
(reentrenar en ene 2027 en vez de quedarse en vigilar) y el ingreso promedio
queda estable. Las variables que el escenario no toca (retiros, P2P,
antiguedad) dan lo mismo que antes.

**Etiqueta de impago.** Sus coeficientes pre-registrados no cambian. Sus
entradas observadas (capacidad de ahorro y ratio de endeudamiento) salen del
feature store, asi que en las entregas pasan a usar la ventana. Es un cambio
en la definicion de las features, no en los parametros de la etiqueta.

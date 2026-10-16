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

**Dilucion por promediar sobre todo el historial.** Casi todas las features
se promedian sobre la rejilla completa cliente x mes
(`src/gold/kpi_definitions.py`). Con 24 meses acumulados, un cambio en los
ultimos 3 apenas mueve el promedio, y el PSI lo detecta tarde aunque el
calculo sea correcto. Solo `gasto_promedio_3_meses` mira una ventana
reciente. El escenario de deriva lo confirmo con datos (seccion siguiente):
es el hallazgo principal de esta prueba, registrado en
`docs/technical-debt.md` ("El PSI reacciona tarde a una deriva reciente").
Corregirlo pasa por medir tambien features de ventana reciente y se cruza
con el rediseno de la etiqueta: la ventana de features deberia ser movil
(por ejemplo, los 6 o 12 meses previos a la fecha de corte), alineada con la
fecha de observacion de la etiqueta, y no todo el historial.

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

**Resultado: el detector reacciona tarde.** PSI con la sesion base s42,
fecha 2026-09-30, 500 clientes, 12 meses, `desde_entrega: 1`:

| Escenario (afectados / ingreso / gasto por mes) | Entregas | Ingreso promedio, todo el historial | Gasto, ultimos 3 meses |
|---|---|---|---|
| 40 % / -5 % / +3 % | 6 | 0.012 estable | 0.033 estable |
| 40 % / -5 % / +3 % | 12 | 0.037 estable | 0.081 estable |
| 60 % / -10 % / +8 % (ejemplo) | 6 | 0.036 estable | 0.186 vigilar |
| 100 % / -15 % / +10 % | 3 | 0.021 estable | 0.239 vigilar |
| 100 % / -15 % / +10 % | 6 | 0.113 vigilar | 0.780 reentrenar |

Estas cifras son una **aproximacion**: se calcularon sobre los CSV
generados, con la misma definicion que los KPIs (nomina y depositos sobre
la rejilla completa cliente x mes; compras de los ultimos 3 meses) y los
cortes por deciles de la sesion base, pero sin correr el pipeline. Antes de
citarlas en el reporte o la presentacion hay que medirlas con el pipeline
real (`demo.sh nueva-entrega` con el escenario activo y el historico de
`data/calidad/deriva_psi/`).

Lo que muestran no depende de la aproximacion: con un deterioro de 5 % al
mes, la feature que promedia todo el historial sigue estable despues de un
ano, mientras que la de ventana reciente se mueve el doble. La causa es la
dilucion descrita arriba, y la solucion es medir tambien features de
ventana reciente (`docs/technical-debt.md`).

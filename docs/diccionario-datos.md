# Diccionario de datos

Columnas de cada entidad, su tipo en Silver, las reglas de calidad que
las validan y el tratamiento de datos personales. Generado a partir del
codigo: `generate_synthetic_sources.py` (origen),
`src/silver/typing_rules.py` (tipos), `config/business_rules.yaml`
(reglas), `config/politica_pii.yaml` (PII) y `src/gold/` (Gold). Si
alguno de esos archivos cambia, este documento debe actualizarse en el
mismo PR.

Convenciones:

- **Tipo**: tipo en Silver. En Bronze todas las columnas de negocio son
  texto, tal como llegaron.
- **PII**: `hash+mascara` se reemplaza por `<col>_hash` y `<col>_mascara`;
  `hash` se reemplaza por `<col>_hash`; `descarta` no llega a Silver.
  Detalle en `docs/seguridad.md`.

## Columnas de trazabilidad (todas las entidades en Bronze)

| Columna | Descripcion |
|---|---|
| `_ingestion_timestamp` | Momento de la ingesta. Silver usa el mas reciente para deduplicar |
| `_source_file` | Archivo de origen (nombre, sin ruta) |
| `_source_format` | `csv` o `json` |
| `_sesion_id` | Sesion o entrega que trajo la fila (ver `data/sesiones/<id>/manifest.json`) |

En cuarentena se agregan `_row_id_tmp` y `_motivo_cuarentena` (las
reglas violadas, separadas por `;`).

## clientes

Origen: `clientes.csv`. Llave: `cliente_id`.

| Columna | Tipo | Reglas | PII | Descripcion |
|---|---|---|---|---|
| `cliente_id` | string | not_null, unique | | Identificador sintetico `CLI-######` |
| `nombre` | string | not_null | hash | Nombre(s) de pila |
| `apellido_paterno` | string | not_null | hash | |
| `apellido_materno` | string | | hash | |
| `sexo` | string | not_null, valores `H`, `M` | | Como en la CURP |
| `fecha_nacimiento` | date | not_null, no futura | | Entre 18 y 70 anos antes de la fecha de referencia |
| `estado_nacimiento` | string | | | Clave de entidad de la CURP (`SL`, `DF`, ...; `NE` = extranjero) |
| `curp` | string | not_null, unique, identificador CURP | hash+mascara | Formato, digito verificador y fecha coherente con `fecha_nacimiento` |
| `rfc` | string | unique, identificador RFC, obligatorio si percibe ingresos | hash+mascara | Persona fisica con homoclave del SAT. Vacio para estudiantes |
| `ocupacion` | string | | | Empleado, Independiente, Empresario, Estudiante, Jubilado, Profesionista, Comerciante |
| `ingreso_mensual_declarado` | decimal(12,2) | not_null, >= 0.01 | | MXN |
| `telefono` | string | not_null, 10 digitos sin 0/1 inicial | hash+mascara | Lada real de la ciudad; numero local inicia en 0 (no asignable en Mexico) |
| `email` | string | formato de correo | hash+mascara | Dominios reservados `example.com/.org/.net` |
| `calle` | string | | descarta | |
| `numero_exterior` | string | | descarta | |
| `colonia` | string | | descarta | |
| `codigo_postal` | string | not_null, 5 digitos | | Rango real de la ciudad. Texto para conservar el 0 inicial (CDMX) |
| `municipio` | string | | | |
| `estado` | string | | | Estado de residencia |
| `ciudad` | string | not_null | | Una de 10 ciudades |
| `fecha_alta` | date | not_null, no futura | | Alta como cliente |

## cuentas

Origen: `cuentas.json`. Llave: `cuenta_id`. Un cliente tiene una o mas
cuentas: todos tienen `cuenta_digital`; ~55% `tarjeta_credito`; ~30%
`prestamo_personal`; ~15% `cetes`.

| Columna | Tipo | Reglas | Descripcion |
|---|---|---|---|
| `cuenta_id` | string | not_null, unique | `CTA-######` |
| `cliente_id` | string | not_null, llave foranea a clientes | |
| `tipo_cuenta` | string | not_null, valores `cuenta_digital`, `tarjeta_credito`, `prestamo_personal`, `cetes` | |
| `fecha_apertura` | date | not_null | |
| `saldo_actual` | decimal(12,2) | not_null | MXN. En credito y prestamo, saldo adeudado |
| `limite_credito` | decimal(12,2) | obligatorio si `tarjeta_credito` | MXN |
| `monto_original` | decimal(12,2) | obligatorio si `prestamo_personal` | MXN |
| `plazo_meses` | int | | Plazo del prestamo |
| `moneda` | string | | |
| `estatus` | string | | |

## cetes_inversiones

Origen: `cetes_inversiones.csv`. Una fila por cuenta tipo `cetes`.

| Columna | Tipo | Reglas | Descripcion |
|---|---|---|---|
| `cuenta_id` | string | not_null, llave foranea a cuentas | |
| `monto_invertido` | decimal(12,2) | not_null, >= 0.01 | MXN |
| `plazo_dias` | string | not_null, valores 28, 91, 182, 364 | |
| `tasa_interes_anual` | decimal(6,4) | | |
| `fecha_inicio` | date | not_null | |
| `fecha_vencimiento` | date | not_null | `fecha_inicio` + `plazo_dias` |

## transacciones

Origen: `transacciones/transacciones_AAAA_MM.csv`, un archivo por mes.
Llave: `transaccion_id`. Bronze particiona por `anio_mes`.

| Columna | Tipo | Reglas | PII | Descripcion |
|---|---|---|---|---|
| `transaccion_id` | string | not_null, unique | | `TX-AAAAMM-#######` |
| `cuenta_id` | string | not_null, llave foranea a cuentas | | |
| `cliente_id` | string | | | |
| `fecha` | date | not_null | | Dias 1-28; nomina el 15 y el ultimo dia del mes |
| `tipo_transaccion` | string | not_null, valores `compra`, `p2p_enviado`, `p2p_recibido`, `pago_recurrente`, `nomina`, `retiro` | | |
| `categoria` | string | | | Categoria de gasto o del movimiento |
| `monto` | decimal(12,2) | not_null, distinto de 0 | | MXN. Negativo = salida de dinero |
| `contraparte` | string | | | Descripcion no personal: comercio, "Depósito de Nómina", "Transferencia SPEI", etc. |
| `contraparte_persona` | string | | hash | Nombre de la otra persona, solo en P2P. No es cliente del banco |
| `anio_mes` | string | | | Particion (`AAAA-MM`), derivada de `fecha` en Bronze |

## catalogo_productos

Origen: `catalogo_productos.json` (datos maestros). Solo en Bronze; no
se procesa en Silver.

| Columna | Descripcion |
|---|---|
| `producto_id`, `nombre` | Producto financiero |
| `tasa_interes_anual` | |
| `requiere_ingreso_minimo` | |
| `limite_credito_min`, `limite_credito_max` | Tarjeta de credito |
| `plazos_meses`, `plazos_dias` | Listas de plazos ofrecidos |

## Gold: gold_kpis

`data/gold/kpis.duckdb`. Formato largo: una fila por (cliente, KPI), para
que agregar un KPI no cambie el esquema.

| Columna | Descripcion |
|---|---|
| `cliente_id` | |
| `kpi_id` | Identificador del KPI (tabla siguiente) |
| `categoria`, `nombre` | Del catalogo `config/kpi_catalog.yaml` |
| `valor_numerico` | Valor del KPI numerico |
| `valor_texto` | Valor del KPI categorico |
| `fecha_calculo` | |

| `kpi_id` | Categoria | Unidad | Calculo |
|---|---|---|---|
| `ingreso_mensual_promedio` | ingresos | MXN | Promedio mensual de depositos de nomina |
| `estabilidad_ingreso` | ingresos | MXN | Desviacion estandar del ingreso mensual por nomina |
| `gasto_mensual_total` | gastos | MXN | Promedio mensual de compras |
| `gasto_promedio_3_meses` | gastos | MXN | Promedio mensual de compras de los 3 meses mas recientes |
| `categoria_gasto_dominante` | gastos | categoria | Categoria con mayor gasto en compras |
| `capacidad_ahorro` | ahorro_y_liquidez | MXN | Ingreso mensual promedio menos gasto mensual promedio |
| `saldo_liquido_disponible` | ahorro_y_liquidez | MXN | Saldo de la cuenta digital |
| `ratio_endeudamiento` | riesgo_y_endeudamiento | ratio | Saldo de tarjeta y prestamo entre ingreso mensual promedio |
| `uso_linea_credito` | riesgo_y_endeudamiento | ratio | Saldo de tarjeta entre limite de credito |
| `probabilidad_impago` | riesgo_y_endeudamiento | probabilidad | Salida del modelo de riesgo (`predict_risk.py`) |
| `frecuencia_transacciones` | comportamiento_transaccional | conteo mensual | Promedio de movimientos por mes |
| `actividad_p2p` | comportamiento_transaccional | MXN | Promedio mensual del neto P2P (recibido menos enviado) |

## Gold: gold_features_cliente

Feature store: una fila por cliente, en el mismo DuckDB. Lo leen el
modelo de riesgo y los consumidores (dashboard, simulador, asistente).
Sin PII.

| Columna | Origen | Descripcion |
|---|---|---|
| `cliente_id` | | |
| Los 11 KPIs de `gold_kpis` salvo `probabilidad_impago` | Gold | Una columna por KPI. `ratio_endeudamiento` y `uso_linea_credito` valen 0 si el cliente no tiene el producto |
| `edad_anios` | Silver | Contra la fecha de corte de los datos (ultima transaccion), no contra la fecha del sistema |
| `antiguedad_dias` | Silver | Dias desde `fecha_alta`, contra la fecha de corte |
| `ingreso_declarado` | Silver | `ingreso_mensual_declarado` |
| `numero_productos` | Silver | Tipos de cuenta distintos |
| `tiene_tarjeta_credito`, `tiene_prestamo_personal` | Silver | 1 / 0 |
| `proporcion_retiros` | Silver | Retiros entre total de movimientos |
| `fecha_calculo` | | Metadata; se excluye al entrenar |

## Modelo de riesgo

| Artefacto | Contenido |
|---|---|
| `models/risk_model.joblib` | Clasificador XGBoost entrenado sobre `gold_features_cliente` |
| `models/shap_importancia.png` | Importancia media de cada feature (SHAP) |
| `data/gold/predicciones/probabilidad_impago.parquet` | `cliente_id`, `valor_numerico` |
| `data/labels/risk_labels.parquet` | Etiquetas sinteticas de entrenamiento (`generate_labels.py`) |

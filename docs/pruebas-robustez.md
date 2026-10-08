# Pruebas de robustez

Ademas de las pruebas unitarias del motor de Silver (`tests/test_validation.py`),
se probaron 5 brechas con inyeccion deliberada de datos sucios. 3 de las 5
terminaron en cambios de codigo; las otras 2 confirmaron que el diseno ya era
correcto. El detalle completo (metodo, resultado y razonamiento) esta en el
reporte tecnico del proyecto, fuera del repositorio; este documento resume lo
verificable y dice cuanto de eso esta cubierto hoy por pruebas automatizadas.

Las pruebas se hicieron antes de la ingesta incremental de Bronze, con el volumen
de referencia de entonces (115,797 transacciones).

| Brecha | Que se probo | Hallazgo | Resultado | Prueba automatizada en el repo |
|---|---|---|---|---|
| 1. Archivos mal formados | CSV real de clientes con filas a las que les faltan columnas al final | Spark rellena con NULL sin error y Bronze acepta el archivo. Silver aisla en cuarentena las filas a las que les falta una columna con regla `not_null` (de 8 filas, 7 validas y 1 en cuarentena). Una fila a la que solo le falta `email` pasa como valida, porque `email` no tiene `not_null` | Sin cambio de codigo; `email` se mantiene opcional por decision | No |
| 2. Corrupcion de esquema | CSV sin la columna `ciudad` | Antes del fix, Spark lanzaba `AnalysisException` (`UNRESOLVED_COLUMN`) y detenia el pipeline sin contexto | Fix: `SchemaValidationError` con la entidad y las columnas faltantes, antes de aplicar reglas (`src/silver/validation.py`) | Si: `test_columna_faltante_lanza_schema_validation_error` |
| 3. Idempotencia | Correr la ingesta dos veces sobre la misma fuente, y un `cliente_id` duplicado a proposito con distinto timestamp | 500 filas en ambas corridas; `deduplicate()` conservo la version mas reciente | Sin cambio de codigo. Hoy la idempotencia viene del registro de control de Bronze (`_control_ingesta.jsonl`), no de `overwrite`; se verifico de nuevo el 2026-10-08 (volver a correr `demo.sh` sobre una instancia poblada ingiere 0 archivos) | Parcial: `test_landing.py` cubre el registro de control |
| 4. Entidad faltante | Silver contra un Bronze al que le falta una entidad completa | El error de Spark era legible, pero el log no tenia entrada para la entidad que fallo | Fix: estatus `FAILED` en el log y `RuntimeError` con el nombre de la entidad (`transform_silver.py`) | No |
| 5. Volumen | 500,000 y 1,000,000 de filas con violaciones inyectadas (a 500,000: 1,000 `not_null`, 500 `unique` y 750 `foreign_keys`; al doble a 1,000,000) | Conteos exactos, sin falsos positivos ni negativos. A 500,000 filas tardaba 47.00 s | Fix de rendimiento: regla `unique` con funcion de ventana (un shuffle en vez de dos) y `broadcast` en `foreign_keys`. 30.38 s a 500,000 (-35.4 %) y 43.17 s a 1,000,000: con el doble de datos, 1.42 veces el tiempo | Solo la correccion de nulos (abajo); las pruebas de volumen no estan en el repo |

**Correccion posterior sobre la brecha 5.** La regla `unique` anterior (agrupar y
unir) perdia en silencio las filas con NULL duplicado, porque en un join un NULL
nunca es igual a otro NULL. La version con ventana corrige ese defecto sin
haberlo buscado. Nunca se manifesto en una corrida real porque `business_rules.yaml`
siempre acompana `unique` con `not_null` en la misma columna. Cubierta por
`test_unique_con_nulos_duplicados_no_desaparece`.

**Limites que conviene tener presentes**
- Las pruebas de volumen corrieron en modo local (`local[*]`) en una sola maquina;
  no hay evidencia de comportamiento en un cluster.
- De las 5 brechas, solo la 2 y la correccion de nulos de la 5 tienen una prueba
  automatizada que corre en cada Pull Request. Las demas se hicieron a mano y
  estan documentadas, pero hoy no protegen contra regresiones.

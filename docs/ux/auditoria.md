# Auditoria de usabilidad del dashboard (2026-11-13)

Revision de la capa de presentacion del dashboard Streamlit antes de la demo
del 30 de noviembre. No cambia codigo: describe lo que hay y propone que
cambiar. Las propuestas son solo de presentacion; donde una mejora tocaria
datos, el modelo o las herramientas del asistente, se dice en la columna de
propuesta y queda fuera de este trabajo.

## Como se hizo

- Codigo revisado: `dashboard/app.py`, `dashboard/acceso.py`,
  `dashboard/comun.py`, `dashboard/vista_cliente.py`, las seis paginas de
  `dashboard/paginas/` y `.streamlit/config.toml`. Streamlit 1.66.0.
- App levantada desde `develop` (b0dff70) sobre la instancia de la demo
  (corte al 30 abr 2027, 500 clientes), en solo lectura.
- Capturas con Playwright (`experimentos/capturas_ux.py`) en
  [antes/](antes/), a 1440 px (escritorio) y 1280 px (laptop), pantalla
  completa:

| Captura | Pantalla |
|---|---|
| 01 | Login |
| 02 | Login con contraseña incorrecta |
| 03 | Admin, Portafolio |
| 04 | Admin, Cliente (CLI-000283) |
| 05 | Admin, Vista del cliente |
| 06 | Admin, Simulador de hipoteca |
| 07 | Admin, Asistente al entrar |
| 08 | Admin, Observabilidad |
| 09 | Cliente CLI-000283, Mi resumen |
| 10 | Cliente, Asistente al entrar |
| 11 | Cliente, Asistente mientras responde (solo escritorio) |
| 12 | Cliente, Asistente con respuesta (solo escritorio) |
| 13 | Sin datos en Gold, Portafolio (admin) |
| 14 | Sin datos en Gold, Mi resumen (cliente) |

  El estado sin datos se tomo de una copia del repo sin Gold y solo con
  los usuarios, no de la instancia de la demo.
- Contraste medido sobre los colores que calcula el navegador (WCAG 2.1;
  4.5:1 para texto normal, 3:1 para elementos graficos).

Heuristicas de Nielsen, abreviadas en la tabla: H1 visibilidad del estado,
H2 lenguaje del usuario, H3 control y libertad, H4 consistencia y estandares,
H5 prevencion de errores, H6 reconocer antes que recordar, H7 flexibilidad,
H8 diseño minimalista, H9 recuperarse de errores, H10 ayuda. Criterios
adicionales: JV jerarquia visual, LG lenguaje no tecnico, EST estados de
carga, vacio y error, CON consistencia entre paginas, ACC accesibilidad.

## Hallazgos

Orden: severidad alta primero y, dentro de cada severidad, esfuerzo S antes
que M y L.

| # | Pantalla | Problema | Heuristica | Severidad | Esfuerzo | Propuesta concreta |
|---|---|---|---|---|---|---|
| 1 | Asistente (cliente) | La pregunta de ejemplo "¿En qué se me va el dinero?" se contesto con "No hay suficiente información para determinar en qué se va el dinero" (captura 12). Un boton sugerido que falla es lo primero que se prueba en la demo. | H5, EST | alta | S | Usar como ejemplos solo preguntas que pasaron 9 de 9 en la ultima evaluacion (`docs/evaluacion-asistente.md`), distintas por rol. No se toca el modelo ni las herramientas: solo la lista de botones. |
| 2 | Mi resumen, Asistente, Simulador (cliente) | Sin Gold, el cliente lee "Corre el pipeline completo (`./demo.sh` o `uv run python main.py --help`)" (captura 14). Si Ollama no responde, el cliente ve "brew services start ollama". | H2, H9, LG, EST | alta | S | Mensaje distinto por rol: el cliente lee "Tus datos todavía no están listos. Vuelve a intentarlo más tarde."; el admin conserva la instruccion tecnica. Para Ollama, la pagina captura `ErrorConexion` y muestra "El asistente no está disponible en este momento" al cliente; el detalle tecnico solo al admin. |
| 3 | Todas | Una excepcion no capturada muestra el traceback completo al usuario: `client.showErrorDetails` no esta configurado en `.streamlit/config.toml`. | H9, EST | alta | S | `showErrorDetails = "none"` en `[client]` y un `estado_error()` comun con texto en lenguaje humano (fase 2). |
| 4 | Asistente | Las cuatro preguntas de ejemplo son las mismas para admin y cliente, en primera persona ("mi riesgo") aunque el admin pregunte por otra persona, y ninguna es del portafolio, que es lo que solo el admin puede preguntar y lo que muestra el guion. | H2, H6 | alta | S | Ejemplos por rol. Admin: "¿Cuántos clientes están en riesgo alto?" y dos sobre el cliente elegido en tercera persona. Cliente: preguntas en primera persona, verificadas como en el punto 1. |
| 5 | Mi resumen | Tres cifras distintas responden a "¿cuanto gasto de mas?" sin decir de que periodo es cada una: "Te quedó -$54,046" (este mes), "En un mes normal gastas $13,336 más" (promedio de meses anteriores) y "Gastas $20,387 más de lo que te entra al mes" (promedio de 12 meses, en "Tu riesgo"). Un cliente lo lee como contradiccion. | H4, LG, CON | alta | S | Nombrar el periodo junto a cada cifra ("este mes", "en un mes normal de los últimos 12 meses", "en promedio en el último año"). Solo cambia el texto; los calculos y la ventana siguen igual. |
| 6 | Mi resumen | "Este mes" no muestra el saldo disponible ($15,216 para CLI-000283), aunque la vista del admin si lo muestra. A "¿cómo voy?" le falta la cifra que el cliente busca primero. | JV, CON | alta | S | Agregar "Tienes disponible" como primera cifra de la tarjeta. El dato ya esta en la fila del cliente (`saldo_liquido_disponible`); no hay consulta nueva. |
| 7 | Mi resumen | "Te quedó -$54,046": el verbo "quedar" con un monto negativo no se entiende. "Te entró" y "Salió" ademas no son paralelos. | H2, LG | alta | S | "Entró", "Salió" y "Diferencia", o con signo explicito: "Gastaste $54,046 más de lo que entró". |
| 8 | Portafolio | "Clientes a revisar", la respuesta a la pregunta que la pagina dice responder ("a quién tengo que mirar"), esta al final, despues de cuatro graficas (captura 03, a 2,300 px de alto). | JV, H8 | alta | M | Subir la tabla justo debajo de los KPIs y pasar segmentos, flujo y distribucion a tabs. Se mantiene la seleccion de fila que abre al cliente (paso del guion). |
| 9 | Observabilidad | El hallazgo que importa (3 variables en reentrenar) esta al final de la pagina, debajo de tablas que en la demo siempre dicen "Correcto" (captura 08). | JV, H8 | alta | M | Una fila de estado arriba (capas, coherencia, alertas, deriva) y el detalle en tabs. |
| 10 | Portafolio, Cliente, Mi resumen | El porcentaje se escribe de tres formas: "57 %" (KPI), "47%" (etiquetas de grafica) y "65.45%" (tabla). | H4, CON | media | S | `formatear_porcentaje()` unico (fase 2) con un criterio: espacio antes de "%", sin decimales salvo la probabilidad de impago (un decimal). En graficas, el mismo formato via `labelExpr`. |
| 11 | Todas | Los montos usan tres estilos: "$9,797 MXN" (admin), "$32,876" (cliente) y "9,267" sin signo en las tablas, con ejes en "8M", "0M" y "$25k". La regla de este trabajo pide "$12,345.67 MXN" en todo. | H4, CON | media | S | `formatear_mxn()` en `dashboard/ui.py` (fase 2) para todas las vistas y tablas (`column_config` con formato de pesos). Requiere decision: ver "Decisiones pendientes". |
| 12 | Login | No dice que usuario usar. Un cliente no sabe que su usuario es su numero de cliente. | H6, H10 | media | S | Texto bajo el titulo ("Entra con tu número de cliente, por ejemplo CLI-000123") y `placeholder` en el campo. El mensaje de error ya no revela que campo fallo; se conserva. |
| 13 | Todas (despues de entrar) | El rol y la salida estan en un caption gris al pie del menu ("Sesión: admin (admin)", contraste 4.18:1) y el boton "Cerrar sesión" no se distingue del resto. | H1, H3, ACC | media | S | Bloque de sesion arriba del menu: nombre, rol con etiqueta legible ("Administrador", "Cliente") y boton con icono `:material/logout:`. |
| 14 | Todas | El boton "Deploy" y el menu de desarrollador de Streamlit son visibles para el cliente. | H8 | media | S | `toolbarMode = "viewer"` en `[client]` de `.streamlit/config.toml`. |
| 15 | Todas | Los textos secundarios (`st.caption`) tienen contraste 4.34:1 sobre el fondo y 4.18:1 sobre la barra lateral, por debajo de 4.5:1. En esta app los captions llevan informacion necesaria (ventanas, supuestos). | ACC | media | S | Bajar el gris del texto secundario con el tema (fase 2) o mover a texto normal la informacion necesaria. Meta: 4.5:1 o mas. |
| 16 | Asistente (cliente) | El caption de arriba habla de "Modelo local llama3.1:8b en Ollama" y "consultas"; no dice que puede responder. | H2, H10, LG | media | S | Para el cliente: "Respondo sobre tus cuentas, movimientos, crédito y riesgo con tus datos". El detalle del modelo solo para el admin. |
| 17 | Asistente | "Consultando..." no dice que pasa ni cuanto tarda (captura 11), y los botones de ejemplo siguen visibles y activos mientras responde. | H1, EST | media | S | Spinner con texto descriptivo ("Buscando en tus movimientos...") y ocultar los ejemplos en cuanto hay una pregunta en curso. |
| 18 | Asistente | Los botones de ejemplo se cortan con "..." a 1280 px (captura 07): no se lee la pregunta completa. | H6, ACC | media | S | Dos columnas en vez de cuatro, o preguntas mas cortas. |
| 19 | Asistente (admin) | El selector de cliente no tiene etiqueta visible; no queda claro que el asistente contesta sobre ese cliente. | H1, H6 | media | S | `label_visibility="visible"` con "Cliente sobre el que preguntas". |
| 20 | Mi resumen | En "¿Me alcanza para...?" el plazo y la tasa se ven y se pueden editar con "De contado", y el precio sale sin separador de miles ("15000"). | H5, H8 | media | S | Mostrar plazo y tasa solo con "A crédito" (el formulario ya se envia completo) y formato de miles en el campo de precio. |
| 21 | Mi resumen | La grafica de quincenas mide 200 px de alto con un eje de solo dos marcas ($0 y $25k) y meses en vertical; la caida del 52 % que dice el texto casi no se ve. | JV, H8 | media | S | Titulo con la conclusion ("Tu ingreso por quincena bajó desde noviembre"), etiquetas horizontales y mas altura. Fase 3. |
| 22 | Portafolio, Cliente, Observabilidad | Ejes de meses en vertical (captura 03, 04): con 19 meses se leen de lado. | ACC, H8 | media | S | Etiquetas horizontales cada 2 o 3 meses (`labelAngle=0`, `labelOverlap`). |
| 23 | Portafolio | Los captions citan archivos y capas: "fuente: capa Gold (DuckDB)", "config/dashboard.yaml", "config/etiquetas_impago.yaml". | LG, H8 | media | S | Texto para negocio ("Datos al 30 abr 2027") y las rutas en un expander "Cómo se calcula" para quien las busque. |
| 24 | Portafolio | En la tabla, a 1280 px, la ultima columna ("Uso de línea") se corta y la etiqueta del KPI "Probabilidad media de im..." tambien (captura 03 laptop). | ACC | media | S | Menos columnas visibles por defecto (las demas en "Ver todos los clientes") y titulos de KPI mas cortos con la definicion en `help`. |
| 25 | Mi resumen | La explicacion del riesgo dice "Puntaje del modelo: 59 %" y "frente a un cliente promedio", y pone dos factores casi iguales ("Gastas $20,387 más..." y "gastaste $79,825 al mes"). La pantalla del admin la llama "probabilidad de impago". | LG, CON | media | M | Frase sin "modelo" ni "puntaje" ("De cada 100 personas con un perfil como el tuyo, unas 59 se atrasan en algún pago"). Juntar factores repetidos es presentacion; cambiar que factores salen es de `src/gold/explicacion.py` y queda fuera. |
| 26 | Todas | Mismo KPI, nombres distintos: "Probabilidad de impago" / "Prob. de impago" / "Puntaje del modelo" / "riesgo de impago"; "Uso de línea" / "Usas de tu tarjeta"; "Capacidad de ahorro" / "Te quedó". | H4, CON | media | M | Diccionario unico de KPIs (fase 2) con nombre admin, nombre cliente y descripcion de una frase, usado en titulos, tooltips y tablas. Los nombres dentro de las respuestas del asistente salen de las herramientas y no se tocan. |
| 27 | Mi resumen | Las tarjetas de dos columnas tienen alturas distintas y dejan huecos grandes (debajo de "Este mes", captura 09). | JV | baja | S | Se resuelve al reordenar en la fase 3 (resumen arriba a todo lo ancho, detalle en tabs). |
| 28 | Portafolio | La barra "medio" de las bandas (amarillo `#fab219`) tiene contraste 1.79:1 con el fondo. Lleva etiqueta escrita, asi que no depende solo del color, pero se pierde al imprimir o proyectar. | ACC | baja | S | Amarillo mas oscuro o borde de 1 px; el texto de la banda ya va al lado. |
| 29 | Observabilidad | Fechas en tres formatos: "04/11/2026 18:30", "2026-09" y "30 abr 2027". | H4, CON | baja | S | `comun.fecha()` y `comun.nombre_mes()` en todos los ejes y textos. |
| 30 | Asistente | "Cómo se obtuvo" muestra al cliente el JSON crudo de la herramienta. | LG, H8 | baja | S | Para el cliente, solo el nombre legible de lo consultado; el JSON queda para el admin. |
| 31 | Observabilidad | Las variables de deriva se muestran con su nombre tecnico (`capacidad_ahorro`, `gasto_promedio_3_meses`). | LG | baja | S | Nombre legible desde el diccionario de KPIs. Vista solo de admin: mejora futura. |
| 32 | Simulador | Cuatro sliders y dos campos sin agrupar; el caption de la tasa cita "LIVA art. 15, fr. X, inc. d" antes del resultado. | H8, LG | baja | M | Resultado primero, supuestos en un expander. Fuera del alcance pedido (las fases 3 y 4 no incluyen el simulador): mejora futura. |

## Decisiones pendientes antes de la fase 2

1. **Formato de pesos.** La regla de este trabajo pide "$12,345.67 MXN" en
   todas las cantidades. Hoy:
   - el admin ve "$9,797 MXN";
   - el cliente ve "$32,876", sin "MXN" en el texto corrido;
   - las respuestas del asistente usan `formato_mxn` de
     `src/common/formato.py`.

   Si `formatear_mxn` vive solo en el dashboard, las vistas tendran
   centavos y "MXN" y el asistente no, porque cambiar su texto es tocar sus
   herramientas. Propuesta:
   - "$12,345.67 MXN" en KPIs, tablas y tooltips;
   - en las frases para el cliente, el mismo formato sin centavos, porque
     "$54,046.23" dentro de una oracion se lee peor;
   - el asistente se queda como esta.

   Necesito tu decision.
2. **Ruta del modulo.** La fase 2 menciona `src/dashboard/ui.py`. El
   dashboard vive en `dashboard/` (no hay `src/dashboard/`), asi que
   propongo `dashboard/ui.py` junto a `comun.py`.
3. **Cifras del guion.** Varias cifras de [guion-demo.md](../guion-demo.md)
   no coinciden con la instancia actual. El guion ya las marca como "en
   revision", y no son un problema de interfaz:

   | Cifra | Guion | Instancia actual |
   |---|---|---|
   | Bandas | 255 / 159 / 86 | 234 / 192 / 74 |
   | Probabilidad de CLI-000283 | 59.7 % | 59.1 % |
   | Probabilidad media | 15.8 % | 16.1 % |
   | Corridas por capa | 9 | 8 |

   Quedan anotadas para la fase 5.

## Recomendacion para antes del 30 de noviembre

Diez cambios, elegidos por impacto en la demo y riesgo bajo de romper el
guion (ninguno quita una vista ni un paso de [guion-demo.md](../guion-demo.md)):

| Orden | Hallazgos | Fase | Por que antes de la demo |
|---|---|---|---|
| 1 | 1 y 4: ejemplos del asistente por rol y verificados | 4 | Es lo primero que se toca en vivo; hoy uno falla. |
| 2 | 2, 3 y 17: estados de error y carga en lenguaje humano | 2 y 4 | Si algo falla en vivo, la pantalla no debe mostrar un traceback ni un comando. |
| 3 | 14 y 15: ocultar "Deploy" y subir el contraste del texto secundario | 2 | Dos lineas de configuracion; la app deja de verse como prototipo. |
| 4 | 10, 11 y 26: formatos y nombres de KPI unicos | 2 | Lo pide la regla del trabajo y elimina las contradicciones visibles entre vistas. |
| 5 | 5, 6 y 7: "¿cómo voy este mes?" con saldo y periodos explicitos | 3 | Es el paso 3 del guion; hoy tres cifras parecen contradecirse. |
| 6 | 25 y 21: riesgo en frases y graficas con titulo que concluye | 3 | Lo que el cliente tiene que entender sin ayuda. |
| 7 | 8: "Clientes a revisar" arriba en Portafolio | 4 | El guion abre aqui y salta al cliente desde esa tabla. |
| 8 | 12 y 13: login con instruccion y rol visible al entrar | 4 | El guion cambia de admin a cliente; debe ser obvio quien esta dentro. |
| 9 | 16, 18 y 19: el asistente dice que puede responder y sobre quien | 4 | Evita preguntas fuera de alcance en vivo. |
| 10 | 20: "¿Me alcanza para...?" sin campos que no aplican | 3 | Paso del guion ("¿Me alcanza para algo de $15,000?"). |

Mejora futura, despues de la demo:

- 9 (reordenar Observabilidad). Es la seccion de 3 minutos del guion y hoy
  se recorre en orden. Reordenarla cambia el recorrido que ya se ensayo, y
  la pagina no confunde: solo es larga.
- 22, 23, 24, 28, 29: ejes, captions tecnicos, columnas y fechas. Pulido
  sin riesgo, pero ninguno cambia lo que se entiende en la demo. Si sobra
  tiempo en las fases 2 y 4, entran sin costo.
- 27: se resuelve solo con el reordenamiento de la fase 3.
- 30, 31, 32: el asistente para el cliente sin JSON, los nombres de
  variables de deriva y el simulador reordenado. El simulador y
  Observabilidad no estan en las fases pedidas.
- `st.cache_data` en consultas repetidas (fase 4): casi todas las
  consultas a DuckDB ya pasan por cache con la fecha de Gold como llave
  (`dashboard/comun.py`). La que no pasa por cache es
  `salidas_por_categoria` de 3 meses en Mi resumen, que corre en cada
  recarga. Los tiempos de carga no se midieron en esta fase; se miden en
  la fase 4 antes de agregar spinners.

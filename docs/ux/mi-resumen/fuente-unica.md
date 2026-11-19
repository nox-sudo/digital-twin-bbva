# Una sola fuente por dato en Mi resumen

Donde aparece cada cifra de Mi resumen para CLI-000283 (instancia de la demo,
corte abr 2027), antes y despues del cambio. "Antes" es la rama
`feature/ux-resumen-siguiente-paso`; "despues", `feature/ux-resumen-fuente-unica`.
Se verifica con `tests/test_ui.py::test_cada_cifra_vive_en_un_solo_lugar`
(fixtures) y con la instancia de la demo: ningun monto en pesos se repite
entre elementos de la pagina, para CLI-000283 ni para CLI-000379.

| Dato | Cifra | Antes | Despues |
|---|---|---|---|
| Saldo disponible | $15,215.70 MXN | Tarjeta de saldo | Tarjeta de saldo |
| Meses que alcanza el saldo | menos de 1 mes | Tarjeta de saldo | Tarjeta de saldo |
| Lo que salio en abr 2027 | $106,852.34 MXN | "Gastaste este mes"; "Este mes" (cifra "Salió" y frase) | "Gastaste este mes" |
| Lo que entro en abr 2027 | $32,876.34 MXN | "Este mes" (cifra y frase) | "Este mes" (cifra) |
| Diferencia de abr 2027 | -$73,976.00 MXN | "Este mes" (cifra y frase) | "Este mes" (cifra) |
| Promedio de los 18 meses anteriores | $16,658 al mes | "Este mes" (frase) | Se quita: la ventana principal es 12 meses (punto 9) y esa cifra ya esta en el chip |
| Deficit promedio de 12 meses (capacidad de ahorro) | $27,030 al mes | Chip de "Tu siguiente paso"; factor de "Tu riesgo, explicado" | Chip de "Tu siguiente paso"; el factor dice "Gastas más de lo que te entra cada mes", sin monto |
| Meta de pagos de deuda | $10,999 al mes | Accion de "Tu siguiente paso"; lectura de "Tu crédito" | Accion de "Tu siguiente paso" |
| Pagos de deuda al mes | $25,512.94 MXN | Lectura de "Tu crédito" ($25,513); cifra de "Tu crédito" | Cifra de "Tu crédito" |
| Ingreso de los ultimos 3 meses | $36,665 al mes | Lectura de "Tu crédito" | Se quita del texto al cliente (sigue siendo la base de la meta de la escalera) |
| Uso del limite de la tarjeta | 38 % | Cifra de "Tu crédito" | Cifra de "Tu crédito"; si sale como factor del riesgo, va sin porcentaje |
| Nivel de riesgo | Alto | Tarjeta de riesgo; titular de "Tu riesgo, explicado" | Tarjeta de riesgo |
| Probabilidad de impago | 59.7 % | Nota de "Tu riesgo, explicado" | Nota de "Tu riesgo, explicado" (el punto 11 la lleva a la tarjeta de riesgo) |
| Riesgo de quedarse sin dinero (36 meses) | prácticamente seguro | Chip de "Tu siguiente paso" | Chip de "Tu siguiente paso" |
| Gasto por categoria | pago de deuda $76,334 y otros | "A dónde se va tu dinero" (frase y barras) | "A dónde se va tu dinero" (barras; la frase solo nombra las categorias) |
| Categoria que mas crecio | Suscripciones, $10,567 al mes | Tarjeta "Lo que más creció" | Tarjeta "Lo que más creció" |
| Ingreso por quincena | $16,438 | "Tus ingresos por quincena" | "Tus ingresos por quincena" |

La respuesta de "¿Me alcanza para...?" puede repetir el saldo, pero solo
aparece al pedirla y responde a otra pregunta.

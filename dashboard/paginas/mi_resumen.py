"""
Mi resumen: la vista del cliente. Arriba, la respuesta a "¿como voy?": su
saldo disponible, lo que gasto este mes, la categoria que mas crecio, su
nivel de riesgo y su siguiente paso (reglas sobre la escalera financiera y
el Monte Carlo del simulador, sin modelo de lenguaje). Debajo, el detalle:
lo que entro y salio este mes, sus ingresos por quincena, a donde se va
su dinero, su credito (escalera financiera), su riesgo explicado con sus
propios valores SHAP y "¿me alcanza para...?" con el Monte Carlo del
simulador.

Un cliente solo ve la suya; el admin la abre como "Vista del cliente" y
elige a quien. Las frases salen de dashboard/vista_cliente.py; todas las
cifras las calcula Python.
"""

import altair as alt
import streamlit as st
import yaml

from asistente import herramientas as h
from dashboard import acceso, comun, ui
from dashboard import vista_cliente as vc
from src.gold import escalera as esc
from src.gold import explicacion as ex
from src.simulacion import montecarlo as mc
from src.simulacion.supuestos import cargar_simulacion

usuario = acceso.requiere_rol("admin", "cliente")
st.title("Mi resumen" if usuario.rol == "cliente" else "Vista del cliente")

# Ventana principal de lo que lee el cliente: la de las features
# (config/features.yaml, 12 meses). Otras ventanas solo donde son
# indispensables y con su periodo escrito: "Lo que más creció" (3 meses
# contra los 3 anteriores), el horizonte del modelo de riesgo (6 meses) y el
# Monte Carlo (36 meses).
# Periodos que compara la tarjeta "Lo que más creció": los ultimos 3 meses
# contra los 3 anteriores.
MESES_CAMBIO = 3
# Montos del eje en miles ("$10k") para que no se corten en tarjetas
# angostas; el cero como "$0" (el formato "$~s" lo mostraba como "$0k").
EJE_PESOS = alt.Axis(
    labelExpr="datum.value == 0 ? '$0' : '$' + format(datum.value / 1000, ',.0f') + 'k'",
    tickCount=4,
)


@st.cache_data
def config_simulacion() -> dict:
    return cargar_simulacion(comun.RAIZ / "config" / "simulacion.yaml")


def contexto(cliente_id: str) -> h.Contexto:
    """Mismas rutas que el resto del dashboard (comun.config). El rol es el
    de la sesion; la pagina solo usa evaluar_compra, que tienen los dos."""
    cfg = comun.config()
    return h.Contexto(
        cliente_id=cliente_id,
        ruta_gold=cfg["gold_duckdb"],
        ruta_resumen=cfg["resumen_portafolio"],
        bandas=cfg["bandas_riesgo"],
        ventana_meses=cfg["ventana_meses"],
        simulacion=config_simulacion(),
        rol=acceso.usuario_actual().rol,
    )


@st.cache_data(show_spinner=False)
def quincenas(version: float, cliente_id: str):
    cfg = comun.config()
    return comun.datos.ingresos_quincenales(
        cfg["gold_duckdb"], cliente_id, cfg["ventana_meses"]
    )


@st.cache_data(show_spinner=False)
def creditos(version: float, cliente_id: str):
    return comun.datos.creditos_cliente(comun.config()["gold_duckdb"], cliente_id)


@st.cache_data(show_spinner=False)
def escalera(version: float, cliente_id: str) -> esc.Escalera:
    cfg = comun.config()
    return esc.calcular(cfg["gold_duckdb"], cliente_id, cfg["ventana_meses"])


@st.cache_data(show_spinner=False)
def explicacion(version: float, cliente_id: str):
    """Explicacion SHAP, o el motivo por el que no la hay."""
    cfg = comun.config()
    try:
        return ex.explicar_cliente(
            cfg["gold_duckdb"],
            cfg["modelo"],
            cfg["probabilidad_oof"],
            cfg["modelos_pliegues"],
            cliente_id,
        )
    except ex.SinExplicacion as e:
        return str(e)


@st.cache_data(show_spinner=False)
def cambios(version: float, cliente_id: str):
    return comun.datos.cambio_por_categoria(
        comun.config()["gold_duckdb"], cliente_id, MESES_CAMBIO
    )


@st.cache_data
def horizonte_modelo() -> int:
    """Meses del horizonte de la etiqueta de impago, para decir el riesgo en
    un plazo concreto."""
    with open(comun.RAIZ / "config" / "etiquetas_impago.yaml", encoding="utf-8") as f:
        return int(yaml.safe_load(f)["horizonte_meses"])


@st.cache_data(show_spinner=False)
def riesgo_flujo_actual(version: float, cliente_id: str) -> tuple[float, int] | None:
    """Probabilidad de quedar en saldo negativo algun mes con el flujo
    actual, sin compras ni hipoteca: el mismo calculo y los mismos supuestos
    que "Riesgo sin hipoteca" en el Simulador de hipoteca."""
    cfg = config_simulacion()
    choque = cfg["choque_ingreso"]
    supuestos = mc.Supuestos(
        meses=int(cfg["simulacion"]["meses"]),
        trayectorias=int(cfg["simulacion"]["trayectorias"]),
        semilla=int(cfg["simulacion"]["semilla"]),
        prob_choque_mensual=float(choque["probabilidad_mensual"]),
        duracion_choque=tuple(int(x) for x in choque["duracion_meses"]),
    )
    gold = comun.config()["gold_duckdb"]
    try:
        historial = mc.historial_cliente(gold, cliente_id)
        saldo = mc.saldo_inicial_cliente(gold, cliente_id)
    except Exception:
        return None
    if not len(historial.meses):
        return None
    muestras = mc.muestrear(len(historial.meses), supuestos)
    resultado = mc.resumir(mc.simular(historial, saldo, muestras), horizonte_modelo())
    return resultado.probabilidad_deficit, supuestos.meses


# La vista del admin ("Vista del cliente") muestra lo mismo que ve el cliente:
# los nombres de KPI son siempre los del cliente.
md = ui.md


def cifras(pares: list[tuple[str, str]]) -> None:
    """Cifras grandes con su etiqueta, que bajan de renglon cuando la tarjeta
    es angosta (st.metric en columnas corta los montos con "..."). Dentro de
    HTML el escape "\\$" se veria literal: el signo va como entidad."""
    bloques = "".join(
        "<div><div style='font-size:0.85rem;opacity:0.7'>"
        f"{etiqueta}</div><div style='font-size:1.5rem;font-weight:600'>"
        f"{valor.replace('$', '&#36;')}</div></div>"
        for etiqueta, valor in pares
    )
    st.markdown(
        f"<div style='display:flex;flex-wrap:wrap;gap:8px 28px;margin-bottom:8px'>"
        f"{bloques}</div>",
        unsafe_allow_html=True,
    )


def titulo_quincenas(lectura: dict) -> str:
    """Titulo de la grafica con la conclusion, no el nombre de la metrica."""
    cambio = lectura.get("cambio") or 0.0
    if cambio <= -vc.CAMBIO_RELEVANTE:
        return "Tu ingreso por quincena bajó"
    if cambio >= vc.CAMBIO_RELEVANTE:
        return "Tu ingreso por quincena subió"
    return "Tu ingreso por quincena se mantiene"


def pendiente(que: str) -> None:
    st.caption(f"{que} estará disponible después de la próxima corrida del pipeline.")


if comun.gold_disponible():
    cfg = comun.config()
    version = comun.version_gold()
    df = comun.portafolio(version)
    cliente_id = acceso.cliente_actual(df["cliente_id"].tolist())
    if usuario.rol == "admin":
        st.caption(f"Así ve {cliente_id} su resumen.")
    fila = df.set_index("cliente_id").loc[cliente_id]

    flujo_cliente = comun.flujo(version, cliente_id)
    mes = vc.resumen_mes(flujo_cliente, comun.nombre_mes)

    # --- Saludo -----------------------------------------------------------
    # Sin nombre: los nombres solo existen en las fuentes crudas y se guardan
    # como hash desde Silver (config/politica_pii.yaml); Gold y el dashboard
    # no tienen PII. En una app real el nombre vendria del servicio de
    # identidad de la sesion, no de la capa analitica. El admin lo ve igual:
    # "Vista del cliente" muestra lo mismo que ve el cliente.
    cierre = f" al cierre de {comun.nombre_mes(mes['mes'])}" if mes else ""
    st.markdown(
        f"<div style='font-size:1.25rem;margin:-8px 0 12px'>Hola. Así van tus "
        f"finanzas{cierre}.</div>",
        unsafe_allow_html=True,
    )

    # --- ¿Como voy? -------------------------------------------------------
    # Autonomia: saldo entre el deficit promedio de la ventana de 12 meses
    # (capacidad de ahorro negativa), la misma cifra del KPI y de la
    # explicacion del riesgo. Sin deficit no se muestra.
    ventana = cfg["ventana_meses"]
    autonomia = vc.autonomia_meses(
        fila["saldo_liquido_disponible"], fila["capacidad_ahorro"]
    )
    ui.tarjeta_heroe(
        ui.nombre_kpi("saldo_liquido_disponible", "cliente"),
        ui.formatear_mxn(fila["saldo_liquido_disponible"]),
        (
            f"Saldo de tu cuenta al cierre de {comun.nombre_mes(mes['mes'])}"
            if mes
            else "Saldo de tu cuenta al último corte"
        ),
        vc.frase_autonomia(autonomia, ventana),
    )
    gasto = vc.comparacion_gasto(flujo_cliente, ventana)
    crecio = vc.mayor_aumento(
        cambios(version, cliente_id), comun.nombre_categoria, MESES_CAMBIO
    )
    banda = fila["banda_riesgo"]
    # Las tres tarjetas en una cuadricula: misma altura y separacion fija
    # con la tarjeta de saldo y con "Tu siguiente paso".
    ui.fila_tarjetas(
        [
            {
                "titulo": (
                    f"Gastaste este mes ({comun.nombre_mes(mes['mes'])})"
                    if mes
                    else "Gastaste este mes"
                ),
                "valor": (
                    ui.formatear_mxn(gasto["salidas"]) if gasto else "Sin movimientos"
                ),
                "detalle": gasto["frase"] if gasto else None,
            },
            {
                "titulo": "Lo que más creció",
                "valor": crecio["titulo"],
                "detalle": crecio["frase"],
            },
            {
                "titulo": ui.nombre_kpi("banda_riesgo", "cliente"),
                "valor": vc.NIVEL_BANDA.get(banda, "Sin dato"),
                "detalle": vc.TONO_BANDA.get(
                    banda, "Todavía no tenemos tu nivel de riesgo."
                )
                .split(": ")[-1]
                .capitalize(),
                "banda": banda if banda in vc.NIVEL_BANDA else None,
                # Medidor junto a la palabra: la probabilidad y los cortes de
                # las bandas (config/dashboard.yaml). Es el unico lugar de la
                # probabilidad en Mi resumen.
                "extra_html": (
                    ui.html_medidor(
                        fila["probabilidad_impago"],
                        banda,
                        [b["desde"] for b in cfg["bandas_riesgo"] if b["desde"] > 0],
                        f"{ui.formatear_porcentaje(fila['probabilidad_impago'], 1)} de "
                        f"probabilidad en {horizonte_modelo()} meses",
                    )
                    if banda in vc.NIVEL_BANDA
                    else None
                ),
            },
        ]
    )

    # --- Siguiente paso ---------------------------------------------------
    cuentas = creditos(version, cliente_id)
    e = escalera(version, cliente_id) if cuentas is not None else None
    riesgo_flujo = riesgo_flujo_actual(version, cliente_id)
    exp = explicacion(version, cliente_id)
    n_factores = ex.cargar_config()["factores_mostrados"]
    # Sin cifra en los factores cuya cifra ya esta en otra tarjeta (deficit,
    # saldo, uso de la tarjeta): cada cifra vive en un solo lugar.
    riesgo = vc.resumen_riesgo(
        banda,
        exp if isinstance(exp, ex.Explicacion) else None,
        n_factores,
        ventana,
        sin_cifra=True,
    )
    meta = (
        vc.meta_escalera(e.paso_actual, e.metricas, esc.cargar_config()) if e else None
    )
    paso = vc.siguiente_paso_compacto(
        banda,
        fila["saldo_liquido_disponible"],
        fila["capacidad_ahorro"],
        ventana,
        esc.NOMBRES[e.paso_actual] if e else None,
        meta["accion"] if meta else None,
        riesgo_flujo[0] if riesgo_flujo else None,
        riesgo_flujo[1] if riesgo_flujo else 0,
    )
    with ui.contenedor("siguiente-paso"):
        st.caption(":material/flag: Tu siguiente paso")
        ui.bloque_accion(paso["titular"], paso["accion"], paso["chips"])

    st.subheader("El detalle")
    izquierda, derecha = st.columns(2, gap="large")

    # --- Este mes ---------------------------------------------------------
    with ui.contenedor("este-mes", izquierda):
        st.subheader("Este mes")
        if mes is None:
            st.caption("Todavía no hay movimientos en tu cuenta.")
        else:
            # Lo que salio ya esta en "Gastaste este mes": aqui solo lo que
            # entro y la diferencia.
            cifras(
                [
                    ("Entró", ui.formatear_mxn(mes["entradas"])),
                    ("Diferencia", ui.formatear_mxn(mes["neto"])),
                ]
            )
            st.caption(
                "Diferencia: lo que entró menos lo que salió este mes. Lo que salió "
                "está arriba, en «Gastaste este mes»."
            )

    # --- Ingresos por quincena -------------------------------------------
    with ui.contenedor("quincenas", derecha):
        st.subheader("Tus ingresos por quincena")
        q = quincenas(version, cliente_id)
        if q is None:
            pendiente("El detalle por quincena")
        else:
            minimo = esc.cargar_config()["ingreso_variable"]["proporcion_depositos_min"]
            lectura_q = vc.resumen_quincenas(q, minimo, comun.nombre_mes)
            st.markdown(md(lectura_q["frase"]))
            # Los 12 meses de la ventana, la misma de la frase de arriba.
            recientes = q[q["mes"].isin(sorted(q["mes"].unique())[-ventana:])].assign(
                etiqueta=lambda d: d["mes"].map(comun.nombre_mes),
                cual=lambda d: d["quincena"].map(
                    {1: "1.ª quincena", 2: "2.ª quincena"}
                ),
                monto_texto=lambda d: d["monto"].map(ui.formatear_mxn),
            )
            grafica = (
                alt.Chart(recientes)
                .mark_bar(cornerRadiusEnd=4)
                .encode(
                    x=alt.X(
                        "etiqueta:N",
                        title=None,
                        sort=None,
                        axis=alt.Axis(labelAngle=0),
                    ),
                    xOffset="cual:N",
                    y=alt.Y("monto:Q", title=None, axis=EJE_PESOS),
                    color=alt.Color(
                        "cual:N",
                        # Dos tonos de la paleta azul: las quincenas son la
                        # misma serie partida en dos, no dos cosas distintas.
                        scale=alt.Scale(range=[ui.MARINO, comun.AZUL]),
                        legend=alt.Legend(title=None, orient="top"),
                    ),
                    tooltip=[
                        alt.Tooltip("etiqueta:N", title="Mes"),
                        alt.Tooltip("cual:N", title="Quincena"),
                        alt.Tooltip("monto_texto:N", title="Ingreso"),
                    ],
                )
                .properties(height=240, title=titulo_quincenas(lectura_q))
            )
            st.altair_chart(grafica, width="stretch")

    izquierda, derecha = st.columns(2, gap="large")

    # --- A donde se va tu dinero -------------------------------------------
    with ui.contenedor("gastos", izquierda):
        st.subheader("A dónde se va tu dinero")
        salidas = comun.datos.salidas_por_categoria(
            cfg["gold_duckdb"], cliente_id, ventana
        )
        # Los montos van en las barras; la frase solo nombra las categorias.
        st.markdown(
            md(
                vc.resumen_gastos(
                    salidas, comun.nombre_categoria, ventana, con_cifras=False
                )
            )
        )
        if not salidas.empty:
            top = salidas.head(5).assign(
                nombre=lambda d: d["categoria"].map(comun.nombre_categoria),
                total_texto=lambda d: d["total"].map(ui.formatear_mxn),
                proporcion_texto=lambda d: d["proporcion"].map(ui.formatear_porcentaje),
                etiqueta=lambda d: d.apply(
                    lambda f: f"{ui.formatear_mxn(f['total'], centavos=False)} · "
                    f"{ui.formatear_porcentaje(f['proporcion'])}",
                    axis=1,
                ),
            )
            # Valor escrito al final de cada barra; el eje llega mas alla de
            # la barra mas larga para que la etiqueta no se corte.
            base = alt.Chart(top).encode(
                y=alt.Y(
                    "nombre:N", sort="-x", title=None, axis=alt.Axis(labelLimit=140)
                ),
                x=alt.X(
                    "total:Q",
                    title=None,
                    axis=EJE_PESOS,
                    scale=alt.Scale(domain=[0, float(top["total"].max()) * 2.1]),
                ),
                tooltip=[
                    alt.Tooltip("nombre:N", title="Categoría"),
                    alt.Tooltip("total_texto:N", title="Total"),
                    alt.Tooltip("proporcion_texto:N", title="Del total"),
                ],
            )
            st.altair_chart(
                (
                    base.mark_bar(color=comun.AZUL, cornerRadiusEnd=4)
                    + base.mark_text(
                        align="left", dx=6, color=ui.TEXTO_2, fontSize=12
                    ).encode(text="etiqueta:N")
                ).properties(height=alt.Step(30)),
                width="stretch",
            )

    # --- Tu credito ------------------------------------------------------------
    with ui.contenedor("credito", derecha):
        st.subheader("Tu crédito")
        if e is None:
            pendiente("Tu crédito")
        else:
            # Escalera visual: los 6 pasos, el actual resaltado y lo que falta
            # para el siguiente. La meta del paso (la accion) esta en "Tu
            # siguiente paso"; aqui, donde va y cuanto le falta.
            st.markdown(f"Paso {e.numero_paso} de {len(e.pasos)}")
            ui.escalera_visual([(p.paso, p.nombre) for p in e.pasos], e.numero_paso - 1)
            if meta:
                st.markdown(
                    md(f"Para el siguiente paso: {meta['falta'] or meta['hoy']}.")
                )
            m = e.metricas
            cifras(
                [
                    (
                        ui.nombre_kpi("pago_comprometido", "cliente"),
                        ui.formatear_mxn(m["pago_comprometido"]),
                    ),
                    (
                        ui.nombre_kpi("uso_linea_credito", "cliente"),
                        (
                            ui.formatear_porcentaje(m["uso_tarjeta"])
                            if m["tiene_tarjeta"]
                            else "Sin tarjeta"
                        ),
                    ),
                ]
            )
            st.caption("Pago mínimo de tus tarjetas y cuota de tus préstamos, con IVA.")
            if e.supuestos:
                st.caption(vc.lenguaje_cliente(" ".join(e.supuestos)))

    # --- Tu riesgo, explicado --------------------------------------------------
    with ui.contenedor("riesgo"):
        st.subheader("Tu riesgo, explicado")
        # El nivel (alto, medio, bajo) esta en la tarjeta de arriba.
        if isinstance(exp, str):
            st.caption(f"Por qué: {exp}")
        else:
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("Lo que más lo sube")
                for texto in riesgo["suben"] or ["Nada en especial."]:
                    st.markdown(md(f"- {texto}"))
            with c2:
                st.markdown("Lo que más lo baja")
                for texto in riesgo["bajan"] or ["Nada en especial."]:
                    st.markdown(md(f"- {texto}"))
            st.caption(
                "La probabilidad de la tarjeta de riesgo es una estimación de "
                f"atrasarte en algún pago en los próximos {horizonte_modelo()} meses, "
                "no una certeza. Estos son los datos que más pesan en tu caso "
                "comparado con un cliente típico."
            )

    # --- ¿Me alcanza para...? --------------------------------------------------
    with ui.contenedor("me-alcanza"):
        st.subheader("¿Me alcanza para...?")
        # La forma de pago va fuera del formulario: al cambiarla aparecen o
        # desaparecen el plazo y la tasa, que solo aplican a credito.
        forma = st.radio(
            "¿Cómo lo pagas?", ["De contado", "A crédito"], horizontal=True
        )
        plazo, tasa = 12, None
        with st.form("me_alcanza"):
            if forma == "A crédito":
                c1, c2, c3 = st.columns(3)
            else:
                c1, c2, c3 = st.columns(3)[0], None, None
            precio = c1.number_input(
                "¿Cuánto cuesta? (MXN)",
                min_value=0,
                value=15_000,
                step=1_000,
                format="%d",
            )
            if forma == "A crédito":
                plazo = c2.selectbox("Plazo (meses)", [6, 12, 18, 24, 36, 48], index=1)
                tasa = c3.number_input(
                    "Tasa anual que te ofrecen (%)",
                    min_value=0.0,
                    max_value=150.0,
                    value=None,
                    step=1.0,
                    help="Sin tasa no calculamos la cuota.",
                )
            calcular = st.form_submit_button("Calcular", type="primary")
        st.caption("Para una casa, usa el Simulador de hipoteca.")
        if calcular:
            argumentos = {"precio": float(precio)}
            if forma == "De contado":
                argumentos["forma_pago"] = "contado"
            else:
                argumentos.update(forma_pago="credito", plazo_meses=int(plazo))
                if tasa is not None:
                    argumentos["tasa_anual"] = float(tasa) / 100
            if argumentos.get("forma_pago") == "credito" and tasa is None:
                st.warning("Escribe la tasa que te ofrecen para calcular la cuota.")
            else:
                with st.spinner("Simulando tus próximos meses..."):
                    resultado = h.ejecutar(
                        "evaluar_compra", argumentos, contexto(cliente_id)
                    )
                st.markdown(md(vc.resumen_compra(resultado)))

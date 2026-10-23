"""
Mi resumen: la vista del cliente. Lo que entro y salio este mes, sus
ingresos por quincena, a donde se va su dinero, su credito (escalera
financiera), su riesgo explicado con sus propios valores SHAP y "¿me
alcanza para...?" con el Monte Carlo del simulador.

Un cliente solo ve la suya; el admin la abre como "Vista del cliente" y
elige a quien. Las frases salen de dashboard/vista_cliente.py; todas las
cifras las calcula Python.
"""

import altair as alt
import streamlit as st
import yaml

from asistente import herramientas as h
from dashboard import acceso, comun
from dashboard import vista_cliente as vc
from src.gold import escalera as esc
from src.gold import explicacion as ex

usuario = acceso.requiere_rol("admin", "cliente")
st.title("Mi resumen" if usuario.rol == "cliente" else "Vista del cliente")

MESES_GASTO = 3
# Montos del eje en miles ("$10k") para que no se corten en tarjetas
# angostas; el cero como "$0" (el formato "$~s" lo mostraba como "$0k").
EJE_PESOS = alt.Axis(
    labelExpr="datum.value == 0 ? '$0' : '$' + format(datum.value / 1000, ',.0f') + 'k'",
    tickCount=4,
)


@st.cache_data
def config_simulacion() -> dict:
    with open(comun.RAIZ / "config" / "simulacion.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def contexto(cliente_id: str) -> h.Contexto:
    """Mismas rutas que el resto del dashboard (comun.config)."""
    cfg = comun.config()
    return h.Contexto(
        cliente_id=cliente_id,
        ruta_gold=cfg["gold_duckdb"],
        ruta_resumen=cfg["resumen_portafolio"],
        bandas=cfg["bandas_riesgo"],
        ventana_meses=cfg["ventana_meses"],
        simulacion=config_simulacion(),
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


def md(texto: str) -> str:
    """Streamlit lee "$...$" como formula: se escapa el signo de pesos."""
    return texto.replace("$", "\\$")


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

    izquierda, derecha = st.columns(2, gap="large")

    # --- Este mes ---------------------------------------------------------
    with izquierda.container(border=True):
        st.subheader("Este mes")
        mes = vc.resumen_mes(comun.flujo(version, cliente_id), comun.nombre_mes)
        if mes is None:
            st.caption("Todavía no hay movimientos en tu cuenta.")
        else:
            cifras(
                [
                    ("Te entró", vc.pesos(mes["entradas"])),
                    ("Salió", vc.pesos(mes["salidas"])),
                    ("Te quedó", vc.pesos(mes["neto"])),
                ]
            )
            st.markdown(md(mes["frase"]))

    # --- Ingresos por quincena -------------------------------------------
    with derecha.container(border=True):
        st.subheader("Tus ingresos por quincena")
        q = quincenas(version, cliente_id)
        if q is None:
            pendiente("El detalle por quincena")
        else:
            minimo = esc.cargar_config()["ingreso_variable"]["proporcion_depositos_min"]
            st.markdown(md(vc.resumen_quincenas(q, minimo, comun.nombre_mes)["frase"]))
            recientes = q[q["mes"].isin(sorted(q["mes"].unique())[-6:])].assign(
                etiqueta=lambda d: d["mes"].map(comun.nombre_mes),
                cual=lambda d: d["quincena"].map(
                    {1: "1.ª quincena", 2: "2.ª quincena"}
                ),
            )
            grafica = (
                alt.Chart(recientes)
                .mark_bar(cornerRadiusEnd=4)
                .encode(
                    x=alt.X("etiqueta:N", title=None, sort=None),
                    xOffset="cual:N",
                    y=alt.Y("monto:Q", title=None, axis=EJE_PESOS),
                    color=alt.Color(
                        "cual:N",
                        scale=alt.Scale(range=[comun.AZUL, comun.NARANJA]),
                        legend=alt.Legend(title=None, orient="top"),
                    ),
                    tooltip=[
                        alt.Tooltip("etiqueta:N", title="Mes"),
                        alt.Tooltip("cual:N", title="Quincena"),
                        alt.Tooltip("monto:Q", title="Ingreso", format="$,.0f"),
                    ],
                )
                .properties(height=200)
            )
            st.altair_chart(grafica, width="stretch")

    izquierda, derecha = st.columns(2, gap="large")

    # --- A donde se va tu dinero -------------------------------------------
    with izquierda.container(border=True):
        st.subheader("A dónde se va tu dinero")
        salidas = comun.datos.salidas_por_categoria(
            cfg["gold_duckdb"], cliente_id, MESES_GASTO
        )
        st.markdown(md(vc.resumen_gastos(salidas, comun.nombre_categoria, MESES_GASTO)))
        if not salidas.empty:
            top = salidas.head(5).assign(
                nombre=lambda d: d["categoria"].map(comun.nombre_categoria)
            )
            st.altair_chart(
                alt.Chart(top)
                .mark_bar(color=comun.AZUL, cornerRadiusEnd=4)
                .encode(
                    y=alt.Y("nombre:N", sort="-x", title=None),
                    x=alt.X("total:Q", title=None, axis=EJE_PESOS),
                    tooltip=[
                        alt.Tooltip("nombre:N", title="Categoría"),
                        alt.Tooltip("total:Q", title="Total", format="$,.0f"),
                        alt.Tooltip("proporcion:Q", title="Del total", format=".0%"),
                    ],
                )
                .properties(height=alt.Step(26)),
                width="stretch",
            )

    # --- Tu credito ------------------------------------------------------------
    with derecha.container(border=True):
        st.subheader("Tu crédito")
        cuentas = creditos(version, cliente_id)
        if cuentas is None:
            pendiente("Tu crédito")
        else:
            e = escalera(version, cliente_id)
            st.markdown(
                f"**Paso {e.numero_paso} de {len(e.pasos)}: "
                f"{esc.NOMBRES[e.paso_actual]}**"
            )
            st.markdown(md(e.lectura))
            m = e.metricas
            cifras(
                [
                    ("Pagas de deudas al mes", vc.pesos(m["pago_comprometido"])),
                    (
                        "Usas de tu tarjeta",
                        (
                            comun.porcentaje(m["uso_tarjeta"])
                            if m["tiene_tarjeta"]
                            else "Sin tarjeta"
                        ),
                    ),
                ]
            )
            st.caption("Pago mínimo de tus tarjetas y cuota de tus préstamos, con IVA.")
            if e.supuestos:
                st.caption(" ".join(e.supuestos))

    # --- Tu riesgo, explicado --------------------------------------------------
    with st.container(border=True):
        st.subheader("Tu riesgo, explicado")
        exp = explicacion(version, cliente_id)
        n = ex.cargar_config()["factores_mostrados"]
        riesgo = vc.resumen_riesgo(
            fila["banda_riesgo"], exp if isinstance(exp, ex.Explicacion) else None, n
        )
        st.markdown(f"**{riesgo['frase']}**")
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
                f"Puntaje del modelo: {comun.porcentaje(fila['probabilidad_impago'])}. "
                "Es una estimación para ordenar el riesgo, no una certeza. Cada factor "
                "es lo que más mueve tu puntaje frente a un cliente promedio."
            )

    # --- ¿Me alcanza para...? --------------------------------------------------
    with st.container(border=True):
        st.subheader("¿Me alcanza para...?")
        with st.form("me_alcanza"):
            c1, c2, c3, c4 = st.columns(4)
            precio = c1.number_input(
                "¿Cuánto cuesta? (MXN)", min_value=0, value=15_000, step=1_000
            )
            forma = c2.radio("¿Cómo lo pagas?", ["De contado", "A crédito"])
            plazo = c3.selectbox("Plazo (meses)", [6, 12, 18, 24, 36, 48], index=1)
            tasa = c4.number_input(
                "Tasa anual que te ofrecen (%)",
                min_value=0.0,
                max_value=150.0,
                value=None,
                step=1.0,
                help="Solo para crédito. Sin tasa no calculamos la cuota.",
            )
            calcular = st.form_submit_button("Calcular")
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

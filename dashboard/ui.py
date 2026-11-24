"""
dashboard/ui.py

Componentes de interfaz que comparten todas las vistas del dashboard:

  - formatear_mxn() y formatear_porcentaje(): el unico formato de cifras de
    las vistas. Montos como "$12,345.67 MXN" en tarjetas, tablas y
    tooltips; porcentajes como "57 %". Las frases para el cliente
    (dashboard/vista_cliente.py) usan montos sin centavos, porque
    "$54,046.23" dentro de una oracion se lee peor.
  - nombre_kpi() y ayuda(): nombre y descripcion de cada KPI desde el
    diccionario unico (dashboard/kpis.py).
  - tarjeta_kpi(): st.metric con el tooltip que explica el KPI.
  - estado_vacio() y estado_error(): mensajes en lenguaje humano, con el
    detalle tecnico solo para el admin y nunca un traceback.
  - estilos(), tarjeta(), tarjeta_heroe(), semaforo() y contenedor(): el
    aspecto de tarjeta (esquinas redondeadas, sombra suave, mismo
    espaciado) en todas las vistas.

Solo presentacion: ninguna funcion lee datos ni cambia un calculo.

Paleta (los cuatro primeros tambien en .streamlit/config.toml). Contraste
WCAG 2.1 medido contra el fondo en que se usa cada color:

  MARINO     #13315c  primario: botones, seleccion, tarjeta heroe.
                      13:1 con texto blanco encima.
  TURQUESA   #0e7c7b  acento sobre fondo claro (iconos, bordes, cifras
                      positivas). 4.7:1 sobre el fondo y 5.0:1 sobre blanco.
  TURQUESA_CLARO #5eead4  acento dentro de la tarjeta heroe. 8.7:1 sobre
                      el marino.
  FONDO      #f8fafc  fondo de la app.
  FONDO_2    #eef2f7  barra lateral y campos de entrada.
  TARJETA    #ffffff  superficie de tarjetas, con sombra suave.
  TEXTO      #0f172a  texto principal. Al 60 % (st.caption) queda en
                      4.6:1 sobre el fondo.
  TEXTO_2    #475569  texto secundario dentro de tarjetas. 7.6:1 sobre
                      blanco.
  BORDE      #dde3eb  bordes de tarjetas y tablas.

Los colores del semaforo (riesgo bajo, medio, alto) van siempre con su
palabra escrita al lado: el color nunca es la unica señal.
"""

import html
import math

import streamlit as st

from dashboard.kpis import KPIS

MARINO = "#13315c"
TURQUESA = "#0e7c7b"
TURQUESA_CLARO = "#5eead4"
FONDO = "#f8fafc"
FONDO_2 = "#eef2f7"
TARJETA = "#ffffff"
TEXTO = "#0f172a"
TEXTO_2 = "#475569"
BORDE = "#dde3eb"
SOMBRA = "0 1px 2px rgba(15, 23, 42, 0.06), 0 4px 12px rgba(15, 23, 42, 0.06)"
RADIO = "14px"

# Semaforo: fondo suave y texto oscuro del mismo tono (contraste de 7:1 o
# mas en los tres), mas un punto del color pleno.
SEMAFORO = {
    "bajo": {"punto": "#16a34a", "fondo": "#dcfce7", "texto": "#14532d"},
    "medio": {"punto": "#d97706", "fondo": "#fef3c7", "texto": "#78350f"},
    "alto": {"punto": "#dc2626", "fondo": "#fee2e2", "texto": "#7f1d1d"},
    "sin score": {"punto": "#64748b", "fondo": "#e2e8f0", "texto": "#1e293b"},
}

ICONO_VACIO = ":material/hourglass_empty:"
ICONO_ERROR = ":material/error:"
ICONO_AVISO = ":material/warning:"
ICONO_INFO = ":material/info:"


def _sin_dato(valor) -> bool:
    if valor is None:
        return True
    try:
        return math.isnan(float(valor))
    except (TypeError, ValueError):
        return False


def formatear_mxn(valor, centavos: bool = True) -> str:
    """Monto en pesos: "$12,345.67 MXN". Sin centavos ("$12,346 MXN") solo
    donde dos decimales estorban, como ejes. "sin dato" si falta."""
    if _sin_dato(valor):
        return "sin dato"
    decimales = 2 if centavos else 0
    valor = float(valor)
    signo = "-" if round(valor, decimales) < 0 else ""
    return f"{signo}${abs(valor):,.{decimales}f} MXN"


def formatear_porcentaje(valor, decimales: int = 0) -> str:
    """Proporcion (0.57) como porcentaje: "57 %". Un decimal solo para la
    probabilidad de impago, donde la diferencia entre 59.1 % y 59.7 % se
    lee. "sin dato" si falta."""
    if _sin_dato(valor):
        return "sin dato"
    return f"{float(valor) * 100:.{decimales}f} %"


def md(texto: str) -> str:
    """Streamlit lee "$...$" como formula en markdown: se escapa el signo
    de pesos para que un monto se vea como monto."""
    return texto.replace("$", "\\$")


def rol_actual() -> str:
    """Rol del usuario de la sesion. Sin sesion se toma "cliente": el
    mensaje menos tecnico es el seguro por defecto."""
    usuario = st.session_state.get("usuario")
    return getattr(usuario, "rol", "cliente")


def nombre_kpi(clave: str, rol: str | None = None) -> str:
    kpi = KPIS[clave]
    return kpi.admin if (rol or rol_actual()) == "admin" else kpi.cliente


def nombre_variable(variable: str) -> str:
    """Nombre legible de una variable del modelo o del monitoreo de deriva:
    el del diccionario de KPIs (admin) o, si no esta, la misma palabra sin
    guiones bajos."""
    if variable in KPIS:
        return KPIS[variable].admin
    return variable.replace("_", " ").capitalize()


def ayuda(clave: str, extra: str | None = None, **valores) -> str:
    """Descripcion del KPI para el tooltip, con {ventana} y demas valores
    llenos, mas un texto opcional del contexto de la vista."""
    texto = KPIS[clave].descripcion
    if valores:
        texto = texto.format(**valores)
    return f"{texto} {extra}" if extra else texto


def tarjeta_kpi(
    titulo: str,
    valor: str,
    delta: str | None = None,
    ayuda: str | None = None,
    *,
    contenedor=None,
    delta_color: str = "normal",
) -> None:
    """Una cifra con su titulo y, si hay, su cambio y su explicacion en el
    tooltip. El valor llega ya formateado (formatear_mxn o
    formatear_porcentaje) para que la tarjeta no decida formatos."""
    (contenedor or st).metric(
        titulo, valor, delta=delta, delta_color=delta_color, help=ayuda
    )


# Columnas de Gold que una tabla muestra como pesos o como porcentaje. Las
# proporciones se guardan de 0 a 1; la tabla las muestra multiplicadas por
# 100 para escribirlas como "65.5 %", igual que formatear_porcentaje.
COLUMNAS_MXN = (
    "ingreso_mensual_promedio",
    "gasto_mensual_total",
    "gasto_promedio_3_meses",
    "capacidad_ahorro",
    "saldo_liquido_disponible",
    "ingreso_declarado",
)
COLUMNAS_PORCENTAJE = {"probabilidad_impago": 1, "uso_linea_credito": 0}


def columna_mxn(titulo: str):
    """Columna de montos ya convertidos con formatear_mxn. El formato de
    numero de st.dataframe escribe "$-14,309.38" en negativos; como texto
    queda "-$14,309.38 MXN", igual que en el resto de la app."""
    return st.column_config.TextColumn(titulo, alignment="right")


def columna_porcentaje(titulo: str, decimales: int = 0, barra: bool = False):
    """Columna de una proporcion ya multiplicada por 100 (preparar_tabla).
    Con barra, la proporcion se dibuja ademas como barra de 0 a 100."""
    formato = f"%.{decimales}f %%"
    if barra:
        return st.column_config.ProgressColumn(
            titulo, min_value=0.0, max_value=100.0, format=formato
        )
    return st.column_config.NumberColumn(titulo, format=formato)


def preparar_tabla(df, rol: str = "admin"):
    """Copia de la tabla para mostrar y su column_config: los KPIs con su
    nombre del diccionario, los montos en pesos y las proporciones en
    porcentaje. No cambia filas ni columnas, solo como se escriben."""
    vista = df.copy()
    columnas = {}
    for columna in vista.columns:
        titulo = nombre_kpi(columna, rol) if columna in KPIS else None
        if columna in COLUMNAS_MXN:
            vista[columna] = vista[columna].map(formatear_mxn)
            columnas[columna] = columna_mxn(titulo or columna)
        elif columna in COLUMNAS_PORCENTAJE:
            vista[columna] = vista[columna] * 100
            columnas[columna] = columna_porcentaje(
                titulo or columna, COLUMNAS_PORCENTAJE[columna]
            )
        elif columna == "ratio_endeudamiento":
            columnas[columna] = st.column_config.NumberColumn(titulo, format="%.2f")
        elif titulo:
            columnas[columna] = titulo
    return vista, columnas


def estado_vacio(
    mensaje: str, accion_sugerida: str | None = None, *, contenedor=None
) -> None:
    """Todavia no hay nada que mostrar: que pasa y que se puede hacer."""
    texto = mensaje if not accion_sugerida else f"{mensaje} {accion_sugerida}"
    (contenedor or st).info(md(texto), icon=ICONO_VACIO)


def estado_error(mensaje: str, detalle: str | None = None, *, contenedor=None) -> None:
    """Algo fallo. El mensaje es para cualquier usuario; el detalle tecnico
    (la causa, un comando) solo lo ve el admin, plegado."""
    destino = contenedor or st
    destino.error(md(mensaje), icon=ICONO_ERROR)
    if detalle and rol_actual() == "admin":
        with destino.expander("Detalle técnico"):
            st.code(detalle, language=None)


def sin_datos_de_gold() -> None:
    """Gold no existe todavia. El cliente lee que sus datos no estan
    listos; el admin, ademas, como generarlos."""
    if rol_actual() == "admin":
        estado_vacio(
            "Todavía no hay datos en Gold.",
            "Corre el pipeline completo (`./demo.sh` o "
            "`uv run python main.py --help`) y recarga esta página.",
        )
    else:
        estado_vacio(
            "Tus datos todavía no están listos.",
            "Vuelve a intentarlo más tarde.",
        )


# --- Aspecto de tarjeta -------------------------------------------------------

CSS = f"""
<style>
/* Tarjetas de KPI (st.metric) y contenedores marcados con contenedor().
   Un formulario dentro de una tarjeta conserva el borde del tema. */
[data-testid="stMetric"],
[class*="st-key-gd-tarjeta"] {{
  background: {TARJETA};
  border: 1px solid {BORDE};
  border-radius: {RADIO};
  box-shadow: {SOMBRA};
  padding: 16px 18px;
}}
/* Separacion vertical entre bloques de tarjetas, para que no se toquen. */
[class*="st-key-gd-tarjeta"] {{ margin-bottom: 8px; }}
/* Tarjetas que comparten fila (dos columnas) con la misma altura. */
/* En Streamlit 1.66: stColumn > stVerticalBlock > stLayoutWrapper > tarjeta.
   La columna ya mide lo que la mas alta de la fila; el envoltorio crece
   hasta ese alto y la tarjeta lo llena. */
[data-testid="stColumn"] [data-testid="stLayoutWrapper"]:has(> [class*="st-key-gd-tarjeta"]) {{
  flex: 1 1 auto;
}}
[data-testid="stColumn"] [class*="st-key-gd-tarjeta"] {{ height: 100%; }}
/* Notas (st.caption) mas grandes y con mas contraste: TEXTO_2 sobre el
   fondo es 7.2:1 (antes, el texto al 60 %: 4.6:1). */
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {{
  color: {TEXTO_2}; font-size: 0.95rem; opacity: 1;
}}
[data-testid="stMetricValue"] {{ font-weight: 600; }}
[data-testid="stMetricValue"] > div {{ white-space: normal; overflow-wrap: anywhere; }}
.gd-tarjeta {{
  background: {TARJETA};
  border: 1px solid {BORDE};
  border-radius: {RADIO};
  box-shadow: {SOMBRA};
  padding: 16px 18px;
  height: 100%;
}}
.gd-tarjeta .gd-titulo {{ color: {TEXTO_2}; font-size: 0.9rem; margin-bottom: 4px; }}
.gd-tarjeta .gd-valor {{ color: {TEXTO}; font-size: 1.6rem; font-weight: 600;
  line-height: 1.2; overflow-wrap: anywhere; }}
.gd-tarjeta .gd-detalle {{ color: {TEXTO_2}; font-size: 0.9rem; margin-top: 6px; }}
.gd-fila {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin: 16px 0 24px;
}}
.gd-heroe {{
  background: {MARINO};
  border-radius: {RADIO};
  box-shadow: {SOMBRA};
  padding: 22px 24px;
  color: #ffffff;
}}
.gd-heroe .gd-titulo {{ color: #cbd5e1; font-size: 0.95rem; }}
.gd-heroe .gd-valor {{ font-size: 2.4rem; font-weight: 700; line-height: 1.15;
  margin: 4px 0; overflow-wrap: anywhere; }}
.gd-heroe .gd-detalle {{ color: {TURQUESA_CLARO}; font-size: 1rem; }}
.gd-heroe .gd-nota {{ color: #ffffff; font-size: 1rem; font-weight: 600;
  margin-top: 8px; }}
.gd-titular {{ font-size: 1.2rem; font-weight: 600; color: {TEXTO};
  margin: 2px 0 6px; }}
.gd-chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }}
.gd-chip {{ background: {FONDO_2}; color: {TEXTO}; border-radius: 999px;
  padding: 4px 12px; font-size: 0.9rem; }}
.gd-escalera {{ display: flex; flex-direction: column; gap: 6px;
  margin: 8px 0 12px !important; list-style: none; padding: 0 !important; }}
.gd-escalera li {{ margin: 0 !important; padding: 6px 14px 6px 8px !important;
  box-sizing: border-box; overflow: visible; }}
.gd-escalon {{ display: flex; align-items: center; gap: 10px; border-radius: 10px;
  padding: 6px 10px; font-size: 0.95rem; background: {FONDO_2}; color: {TEXTO_2}; }}
.gd-escalon .gd-num {{ flex: 0 0 26px; height: 26px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-weight: 700; background: #ffffff; color: {TEXTO_2}; }}
.gd-escalon .gd-nombre {{ flex: 1; }}
.gd-escalon .gd-estado {{ flex: 0 0 auto; font-size: 0.85rem; white-space: nowrap; }}
.gd-escalon .gd-nombre {{ min-width: 0; }}
.gd-escalon.hecho {{ background: #ccfbf1; color: #134e4a; }}
.gd-escalon.actual {{ background: {MARINO}; color: #ffffff; font-weight: 600; }}
.gd-escalon.actual .gd-num {{ color: {MARINO}; }}
.gd-puntos {{ display: flex; gap: 6px; margin: 2px 0 12px; }}
.gd-puntos span {{ width: 24px; height: 6px; border-radius: 999px;
  background: #cbd5e1; }}
.gd-puntos span.hecho {{ background: {TURQUESA}; }}
.gd-puntos span.actual {{ background: {MARINO}; }}
.gd-meta-titulo {{ font-size: 1.15rem; font-weight: 600; color: {TEXTO};
  margin: 0 0 10px; }}
.gd-avance-barra {{ position: relative; height: 10px; border-radius: 999px;
  background: #e2e8f0; }}
.gd-avance-relleno {{ position: absolute; left: 0; top: 0; bottom: 0;
  border-radius: 999px; background: {SEMAFORO["medio"]["punto"]}; }}
.gd-avance-meta {{ position: absolute; top: -4px; bottom: -4px; width: 2px;
  background: {TEXTO}; }}
.gd-avance-etiquetas {{ display: flex; justify-content: space-between; gap: 12px;
  font-size: 0.9rem; color: {TEXTO_2}; margin: 6px 0 12px; }}
.gd-medidor {{ margin: 8px 0 2px; }}
.gd-medidor-barra {{ position: relative; height: 10px; border-radius: 999px;
  background: {FONDO_2}; overflow: hidden; }}
.gd-medidor-relleno {{ position: absolute; left: 0; top: 0; bottom: 0;
  border-radius: 999px; }}
.gd-corte {{ position: absolute; top: 0; bottom: 0; width: 2px;
  background: #ffffff; }}
.gd-medidor-leyenda {{ font-size: 0.9rem; color: {TEXTO}; margin-top: 4px;
  font-weight: 600; }}
.gd-semaforo {{
  display: inline-flex; align-items: center; gap: 8px;
  border-radius: 999px; padding: 4px 12px; font-weight: 600; font-size: 0.95rem;
}}
.gd-semaforo .gd-punto {{ width: 10px; height: 10px; border-radius: 50%;
  display: inline-block; }}
@media (max-width: 640px) {{
  .gd-heroe .gd-valor {{ font-size: 2rem; }}
  .gd-tarjeta .gd-valor {{ font-size: 1.35rem; }}
}}
</style>
"""


def estilos() -> None:
    """Inyecta el CSS de las tarjetas. Se llama una vez por corrida en
    app.py, antes de la vista."""
    st.html(CSS)


def _texto(valor) -> str:
    """Texto seguro dentro de HTML. El signo de pesos va como entidad: si
    no, Streamlit lo leeria como inicio de una formula."""
    return html.escape(str(valor)).replace("$", "&#36;")


def html_medidor(
    probabilidad: float, banda: str, cortes: list[float], leyenda: str
) -> str:
    """Barra de 0 a 100 % con la probabilidad rellena del color de la banda y
    una marca en cada corte de banda (10 % y 30 %). El porcentaje va escrito
    al lado: la barra no es la unica señal."""
    p = max(0.0, min(1.0, float(probabilidad)))
    color = SEMAFORO.get(banda, SEMAFORO["sin score"])["punto"]
    marcas = "".join(
        f"<span class='gd-corte' style='left:{c * 100:.1f}%'></span>" for c in cortes
    )
    return (
        "<div class='gd-medidor' role='img' "
        f"aria-label='{_texto(leyenda)}'>"
        f"<div class='gd-medidor-barra'><span class='gd-medidor-relleno' "
        f"style='width:{p * 100:.1f}%;background:{color}'></span>{marcas}</div>"
        f"<div class='gd-medidor-leyenda'>{_texto(leyenda)}</div></div>"
    )


def html_puntos(total: int, actual: int) -> str:
    """Una marca por paso: los anteriores al actual como hechos y el actual
    resaltado. El paso en texto va junto ("Paso 2 de 6")."""
    marcas = "".join(
        f"<span class='{'hecho' if i < actual else 'actual' if i == actual else ''}'>"
        "</span>"
        for i in range(total)
    )
    return f"<div class='gd-puntos' aria-hidden='true'>{marcas}</div>"


def html_avance(valor: float, meta: float, hoy: str, objetivo: str) -> str:
    """Barra de avance hacia una meta: valor y meta en fracciones de la escala
    (0 a 1), una marca en la meta y las dos cifras escritas debajo."""
    v = max(0.0, min(1.0, float(valor)))
    t = max(0.0, min(1.0, float(meta)))
    return (
        f"<div class='gd-avance' role='img' aria-label='{_texto(hoy)}. "
        f"{_texto(objetivo)}'>"
        f"<div class='gd-avance-barra'><span class='gd-avance-relleno' "
        f"style='width:{v * 100:.1f}%'></span><span class='gd-avance-meta' "
        f"style='left:calc({t * 100:.1f}% - 1px)'></span></div>"
        f"<div class='gd-avance-etiquetas'><span>{_texto(hoy)}</span>"
        f"<span>{_texto(objetivo)}</span></div></div>"
    )


def html_tarjeta(
    titulo: str,
    valor: str,
    detalle: str | None = None,
    banda: str | None = None,
    extra_html: str | None = None,
) -> str:
    """Con banda, el valor va como semaforo (color y palabra). extra_html va
    entre el valor y el detalle (por ejemplo, un medidor)."""
    cuerpo = html_semaforo(banda, valor) if banda else _texto(valor)
    partes = [
        f"<div class='gd-titulo'>{_texto(titulo)}</div>",
        f"<div class='gd-valor'>{cuerpo}</div>",
    ]
    if extra_html:
        partes.append(extra_html)
    if detalle:
        partes.append(f"<div class='gd-detalle'>{_texto(detalle)}</div>")
    return "<div class='gd-tarjeta'>" + "".join(partes) + "</div>"


def fila_tarjetas(tarjetas: list[dict]) -> None:
    """Tarjetas en una fila con la misma altura: una cuadricula de CSS en vez
    de st.columns, cuyas columnas crecen cada una a su contenido. En pantalla
    angosta bajan una por renglon. Cada tarjeta es un dict con los
    argumentos de html_tarjeta (titulo, valor, detalle, banda)."""
    st.markdown(
        "<div class='gd-fila'>"
        + "".join(html_tarjeta(**t) for t in tarjetas)
        + "</div>",
        unsafe_allow_html=True,
    )


def tarjeta(
    titulo: str,
    valor: str,
    detalle: str | None = None,
    *,
    banda: str | None = None,
    contenedor=None,
) -> None:
    """Una cifra en tarjeta blanca: titulo, valor grande y una linea de
    contexto opcional. Con banda, el valor se dibuja como semaforo."""
    (contenedor or st).markdown(
        html_tarjeta(titulo, valor, detalle, banda), unsafe_allow_html=True
    )


def tarjeta_heroe(
    titulo: str,
    valor: str,
    detalle: str | None = None,
    nota: str | None = None,
    *,
    contenedor=None,
) -> None:
    """La cifra principal de la vista, en la tarjeta marina de arriba, con
    una linea de contexto y una nota opcional debajo."""
    partes = [
        f"<div class='gd-titulo'>{_texto(titulo)}</div>",
        f"<div class='gd-valor'>{_texto(valor)}</div>",
    ]
    if detalle:
        partes.append(f"<div class='gd-detalle'>{_texto(detalle)}</div>")
    if nota:
        partes.append(f"<div class='gd-nota'>{_texto(nota)}</div>")
    (contenedor or st).markdown(
        "<div class='gd-heroe'>" + "".join(partes) + "</div>",
        unsafe_allow_html=True,
    )


def html_semaforo(banda: str, texto: str | None = None) -> str:
    colores = SEMAFORO.get(banda, SEMAFORO["sin score"])
    etiqueta = texto or f"Riesgo {banda}"
    return (
        f"<span class='gd-semaforo' style='background:{colores['fondo']};"
        f"color:{colores['texto']}'><span class='gd-punto' "
        f"style='background:{colores['punto']}'></span>{_texto(etiqueta)}</span>"
    )


def semaforo(banda: str, texto: str | None = None, *, contenedor=None) -> None:
    """Nivel de riesgo como pastilla de color con su palabra escrita."""
    (contenedor or st).markdown(html_semaforo(banda, texto), unsafe_allow_html=True)


ROL_LEGIBLE = {"admin": "Administrador", "cliente": "Cliente"}


def sesion(usuario, al_salir) -> None:
    """Quien esta dentro y como salir, arriba de cada vista y en la barra
    lateral (en el celular la barra empieza cerrada, por eso tambien
    arriba). al_salir se llama al pulsar "Cerrar sesión"."""
    rol = ROL_LEGIBLE.get(usuario.rol, usuario.rol)
    quien = rol if usuario.rol == "admin" else f"{rol} · {usuario.usuario}"
    st.caption(f":material/account_circle: Sesión: {quien}")
    with st.sidebar.container(key="gd-tarjeta-sesion"):
        st.markdown(f"**:material/account_circle: {rol}**")
        if usuario.rol != "admin":
            st.caption(usuario.usuario)
        if st.button(
            "Cerrar sesión",
            icon=":material/logout:",
            use_container_width=True,
            key="cerrar_sesion",
        ):
            al_salir()
            st.rerun()


def bloque_accion(titular: str, accion: str | None, chips: list[str]) -> None:
    """Un titular de una frase, una accion y hasta dos chips (etiquetas
    cortas con una cifra). Sin parrafos largos ni negritas en todo el
    bloque."""
    partes = [f"<div class='gd-titular'>{_texto(titular)}</div>"]
    if accion:
        partes.append(f"<div>{_texto(accion)}</div>")
    if chips:
        partes.append(
            "<div class='gd-chips'>"
            + "".join(f"<span class='gd-chip'>{_texto(c)}</span>" for c in chips[:2])
            + "</div>"
        )
    st.markdown("".join(partes), unsafe_allow_html=True)


def escalera_visual(pasos: list[tuple[str, str]], actual: int) -> None:
    """Los pasos de la escalera financiera, uno por renglon: los anteriores al actual
    como hechos, el actual resaltado y los siguientes pendientes. Cada paso
    dice su estado tambien en texto ("hecho", "estás aquí"), no solo con el
    color. pasos es [(clave, nombre)]; actual, el indice del paso actual."""
    items = []
    for i, (_, nombre) in enumerate(pasos):
        estado, etiqueta = (
            ("hecho", "hecho")
            if i < actual
            else ("actual", "estás aquí") if i == actual else ("", "pendiente")
        )
        items.append(
            f"<li class='gd-escalon {estado}'><span class='gd-num'>{i + 1}</span>"
            f"<span class='gd-nombre'>{_texto(nombre)}</span>"
            f"<span class='gd-estado'>{_texto(etiqueta)}</span></li>"
        )
    st.markdown(
        f"<ol class='gd-escalera' aria-label='Paso {actual + 1} de {len(pasos)}'>"
        + "".join(items)
        + "</ol>",
        unsafe_allow_html=True,
    )


def contenedor(nombre: str, en=None):
    """Un st.container con aspecto de tarjeta, para agrupar graficas o
    formularios. El nombre debe ser unico en la pagina; en es la columna o
    contenedor donde va (por defecto, la pagina)."""
    return (en or st).container(key=f"gd-tarjeta-{nombre}")

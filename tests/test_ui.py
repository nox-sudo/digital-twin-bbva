"""
tests/test_ui.py

Componentes compartidos del dashboard (dashboard/ui.py, dashboard/kpis.py)
y una prueba de humo con AppTest: cada pagina carga sin excepciones para
cada rol que la puede abrir, con datos y sin Gold.

Las vistas corren sobre el Gold minimo de las fixtures, nunca sobre data/.
"""

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

st_testing = pytest.importorskip("streamlit.testing.v1")

from datos_asistente import config_vistas  # noqa: E402
from dashboard import acceso, ui  # noqa: E402
from dashboard.kpis import KPIS  # noqa: E402
from src.common.usuarios import Usuario  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
PAGINAS = RAIZ / "dashboard" / "paginas"
ADMIN = Usuario("admin", "admin")
CLIENTE = Usuario("CLI-1", "cliente")


# --- Formatos ---------------------------------------------------------------


@pytest.mark.parametrize(
    "valor, esperado",
    [
        (12345.678, "$12,345.68 MXN"),
        (-1675, "-$1,675.00 MXN"),
        (0, "$0.00 MXN"),
        (-0.001, "$0.00 MXN"),
        (None, "sin dato"),
        (float("nan"), "sin dato"),
    ],
)
def test_formatear_mxn(valor, esperado):
    assert ui.formatear_mxn(valor) == esperado


def test_formatear_mxn_sin_centavos():
    assert ui.formatear_mxn(54046.23, centavos=False) == "$54,046 MXN"


@pytest.mark.parametrize(
    "valor, decimales, esperado",
    [
        (0.57, 0, "57 %"),
        (0.5913, 1, "59.1 %"),
        (0, 0, "0 %"),
        (None, 0, "sin dato"),
        (float("nan"), 1, "sin dato"),
    ],
)
def test_formatear_porcentaje(valor, decimales, esperado):
    assert ui.formatear_porcentaje(valor, decimales) == esperado


def test_md_escapa_el_signo_de_pesos():
    assert ui.md("$1,000.00 MXN") == "\\$1,000.00 MXN"


# --- Diccionario de KPIs ----------------------------------------------------


def test_cada_kpi_tiene_nombres_y_descripcion():
    for clave, kpi in KPIS.items():
        assert kpi.admin and kpi.cliente, clave
        assert kpi.descripcion.endswith("."), clave
        # Una frase sencilla: sin nombres de columna ni rutas de archivos
        # (los marcadores {ventana} y {tasa_objetivo} los llena ui.ayuda).
        texto = re.sub(r"\{\w+\}", "", kpi.descripcion)
        assert "_" not in texto and "/" not in texto, clave


def test_nombre_y_ayuda_por_rol():
    assert ui.nombre_kpi("capacidad_ahorro", "admin") == "Capacidad de ahorro"
    assert ui.nombre_kpi("capacidad_ahorro", "cliente") == "Te queda al mes"
    texto = ui.ayuda("capacidad_ahorro", "Contexto.", ventana=12)
    assert "12 meses" in texto and texto.endswith("Contexto.")
    assert "{" not in ui.ayuda("probabilidad_media", tasa_objetivo="15 %")


def test_preparar_tabla_solo_cambia_como_se_escribe():
    df = pd.DataFrame(
        {
            "cliente_id": ["CLI-1"],
            "probabilidad_impago": [0.5913],
            "capacidad_ahorro": [-20387.0],
            "otra": [1],
        }
    )
    vista, columnas = ui.preparar_tabla(df)
    assert list(vista.columns) == list(df.columns) and len(vista) == len(df)
    assert vista.loc[0, "probabilidad_impago"] == pytest.approx(59.13)
    assert df.loc[0, "probabilidad_impago"] == 0.5913, "no modifica el original"
    assert vista.loc[0, "capacidad_ahorro"] == "-$20,387.00 MXN"
    assert columnas["probabilidad_impago"]["label"] == "Probabilidad de impago"
    assert "otra" not in columnas


# --- Paginas con AppTest -----------------------------------------------------


@pytest.fixture
def vistas(tmp_path, monkeypatch):
    import streamlit as st

    from dashboard import comun

    cfg = config_vistas(tmp_path)
    monkeypatch.setattr(comun, "config", lambda: cfg)
    st.cache_data.clear()
    return cfg


def correr(pagina, usuario, **estado):
    at = st_testing.AppTest.from_file(str(pagina), default_timeout=120)
    at.session_state["usuario"] = usuario
    for llave, valor in estado.items():
        at.session_state[llave] = valor
    at.run()
    return at


def textos(at) -> str:
    elementos = [*at.title, *at.markdown, *at.caption, *at.info, *at.error]
    return "\n".join(str(e.value) for e in elementos)


# Cada pagina con cada rol que la tiene registrada (acceso.PAGINAS_POR_ROL).
CASOS = [
    (archivo, usuario)
    for usuario in (ADMIN, CLIENTE)
    for lista in acceso.PAGINAS_POR_ROL[usuario.rol].values()
    for archivo, _ in lista
]


@pytest.mark.parametrize(
    "archivo, usuario", CASOS, ids=[f"{a}-{u.rol}" for a, u in CASOS]
)
def test_cada_pagina_carga_sin_excepciones(vistas, archivo, usuario):
    at = correr(PAGINAS / archivo, usuario, cliente_id="CLI-2")
    assert not at.exception, [e.value for e in at.exception]
    assert at.title, "la pagina no mostro su titulo"
    assert "Traceback" not in textos(at)


@pytest.mark.parametrize(
    "archivo, usuario", CASOS, ids=[f"{a}-{u.rol}" for a, u in CASOS]
)
def test_sin_gold_cada_pagina_lo_dice_sin_excepciones(vistas, archivo, usuario):
    Path(vistas["gold_duckdb"]).unlink()
    at = correr(PAGINAS / archivo, usuario)
    assert not at.exception, [e.value for e in at.exception]
    if archivo == "observabilidad.py":
        return  # sin logs de corridas: su propio estado vacio
    avisos = "\n".join(str(i.value) for i in at.info)
    if usuario.rol == "cliente":
        assert "Tus datos todavía no están listos" in avisos
        assert "pipeline" not in avisos and "demo.sh" not in avisos
    else:
        assert "Todavía no hay datos en Gold" in avisos
        assert "demo.sh" in avisos


def test_un_error_no_previsto_no_muestra_el_traceback(vistas, monkeypatch):
    """app.py atrapa la excepcion de la vista: el cliente lee un mensaje en
    lenguaje humano, sin el tipo ni el mensaje del error."""
    from dashboard import comun

    def falla(*args, **kwargs):
        raise RuntimeError("detalle interno de la falla")

    monkeypatch.setattr(comun, "portafolio", falla)
    for usuario, ve_detalle in ((CLIENTE, False), (ADMIN, True)):
        at = correr(RAIZ / "dashboard" / "app.py", usuario)
        assert not at.exception, [e.value for e in at.exception]
        assert "No pudimos cargar esta vista" in at.error[0].value
        detalle = "\n".join(c.value for c in at.code)
        assert ("detalle interno" in detalle) is ve_detalle


def test_asistente_sin_ollama_mensaje_amable(vistas, monkeypatch):
    import streamlit as st

    from asistente import conversacion

    st.cache_resource.clear()

    def responder(*args, **kwargs):
        raise conversacion.ErrorConexion("Ollama no responde en http://x")

    monkeypatch.setattr(conversacion, "ModeloOllama", lambda cfg, rol: object())
    monkeypatch.setattr(conversacion, "responder", responder)
    at = correr(PAGINAS / "asistente.py", CLIENTE)
    at.chat_input[0].set_value("¿En qué gasto?").run()
    assert not at.exception, [e.value for e in at.exception]
    assert "no está disponible" in at.error[0].value
    assert "Ollama" not in at.error[0].value
    assert not at.code, "el cliente no ve el detalle tecnico"


# --- Nombres internos ----------------------------------------------------------

# Un identificador con guion bajo (columna de Gold, clave de configuracion,
# nombre de herramienta), una ruta de archivo o un modulo de Python.
INTERNO = re.compile(
    r"\b[a-z]+(?:_[a-z0-9]+)+\b|\b[\w/]+\.(?:py|yaml|yml|md|duckdb|parquet|joblib)\b"
)


def texto_visible(at) -> str:
    elementos = [
        *at.title,
        *at.header,
        *at.subheader,
        *at.markdown,
        *at.caption,
        *at.info,
        *at.warning,
        *at.error,
        *at.success,
    ]
    salida = [str(e.value) for e in elementos]
    for m in at.metric:
        salida += [str(m.label), str(m.value), str(m.delta or ""), str(m.help or "")]
    for caja in [*at.selectbox, *at.multiselect]:
        salida.append(str(caja.label))
    return "\n".join(salida)


@pytest.mark.parametrize(
    "archivo, usuario", CASOS, ids=[f"{a}-{u.rol}" for a, u in CASOS]
)
def test_ninguna_pagina_muestra_nombres_internos(vistas, archivo, usuario):
    at = correr(PAGINAS / archivo, usuario, cliente_id="CLI-2")
    assert not at.exception, [e.value for e in at.exception]
    # El HTML de las tarjetas lleva clases CSS (gd-tarjeta): se quitan las
    # etiquetas antes de buscar.
    texto = re.sub(r"<[^>]+>", " ", texto_visible(at))
    # Los iconos ":material/nombre:" se dibujan como icono, no como texto.
    texto = re.sub(r":material/\w+:", " ", texto)
    assert INTERNO.findall(texto) == [], archivo


# --- Asistente sin traza tecnica ------------------------------------------------


@pytest.mark.parametrize("usuario", [CLIENTE, ADMIN], ids=["cliente", "admin"])
def test_el_asistente_no_muestra_nombres_de_herramientas(vistas, monkeypatch, usuario):
    """Una respuesta que uso todas las herramientas: la pagina no muestra el
    nombre de ninguna, ni sus argumentos ni su resultado; solo una linea de
    fuente por herramienta, sin repetir."""
    import streamlit as st

    from asistente import conversacion
    from asistente.herramientas import HERRAMIENTAS

    st.cache_resource.clear()
    pasos = [
        conversacion.Paso(nombre, {"argumento_interno": 1}, {"resultado_interno": 2})
        for nombre in [*HERRAMIENTAS, "flujo_mensual"]
    ]

    def responder(*args, **kwargs):
        return conversacion.Respuesta(texto="Listo.", motivo="respuesta", pasos=pasos)

    monkeypatch.setattr(conversacion, "ModeloOllama", lambda cfg, rol: object())
    monkeypatch.setattr(conversacion, "responder", responder)
    at = correr(PAGINAS / "asistente.py", usuario, cliente_id="CLI-1")
    at.chat_input[0].set_value("¿Cómo voy?").run()
    assert not at.exception, [e.value for e in at.exception]

    visible = texto_visible(at) + "\n".join(str(b.label) for b in at.button)
    for nombre in HERRAMIENTAS:
        assert nombre not in visible, nombre
    assert "argumento_interno" not in visible and "resultado_interno" not in visible
    assert not at.expander and not at.json
    assert "Ollama" not in visible and "llama" not in visible

    fuentes = [c.value for c in at.caption][1:]  # la primera describe la pagina
    assert len(fuentes) == len(set(fuentes)), "una frase por fuente, sin repetir"
    persona = "tus movimientos" if usuario.rol == "cliente" else "sus movimientos"
    assert any(persona in f for f in fuentes)


@pytest.mark.parametrize(
    "usuario, ejemplo",
    [
        (CLIENTE, "¿En qué categoría gasto más?"),
        (ADMIN, "¿Cuántos clientes están en riesgo alto?"),
    ],
    ids=["cliente", "admin"],
)
def test_ejemplos_del_asistente_por_rol(vistas, usuario, ejemplo):
    at = correr(PAGINAS / "asistente.py", usuario, cliente_id="CLI-1")
    etiquetas = [b.label for b in at.button]
    assert ejemplo in etiquetas
    otro = "¿Cuántos clientes están en riesgo alto?"
    assert (otro in etiquetas) is (usuario.rol == "admin")


# --- Mi resumen: lo de arriba ------------------------------------------------


def test_cambio_por_categoria(tmp_path):
    from dashboard import datos
    from datos_asistente import crear_gold

    gold = crear_gold(tmp_path)
    cambios = datos.cambio_por_categoria(gold, "CLI-1", 3)
    primera = cambios.iloc[0]
    # Ropa sube 50 al mes: ultimos 3 meses contra los 3 anteriores, +150.
    assert primera["categoria"] == "ropa"
    assert primera["reciente"] == pytest.approx(2600.0)
    assert primera["anterior"] == pytest.approx(2450.0)
    assert primera["cambio"] == pytest.approx(150.0)
    assert cambios.set_index("categoria").loc["comida", "cambio"] == pytest.approx(0.0)


def test_mayor_aumento_y_sin_aumento():
    from dashboard import comun
    from dashboard import vista_cliente as vc

    cambios = pd.DataFrame(
        {
            "categoria": ["ropa", "comida"],
            "reciente": [2600.0, 6000.0],
            "anterior": [2450.0, 6000.0],
            "cambio": [150.0, 0.0],
        }
    )
    r = vc.mayor_aumento(cambios, comun.nombre_categoria, 3)
    assert r["titulo"] == "Ropa" and "$150 más al mes" in r["frase"]
    sin = vc.mayor_aumento(
        cambios.assign(cambio=[-1.0, 0.0]), comun.nombre_categoria, 3
    )
    assert sin["categoria"] is None and sin["titulo"] == "Nada subió"


def test_comparacion_gasto():
    from dashboard import vista_cliente as vc

    flujo = pd.DataFrame(
        {"mes": ["2027-01", "2027-02", "2027-03"], "salidas": [100.0, 100.0, 88.0]}
    )
    r = vc.comparacion_gasto(flujo)
    assert r["salidas"] == 88.0 and r["frase"] == (
        "12 % menos que tu promedio de los 2 meses anteriores."
    )
    assert vc.comparacion_gasto(flujo.iloc[0:0]) is None


def test_siguiente_paso_con_reglas():
    from dashboard import vista_cliente as vc

    texto = vc.siguiente_paso(
        "Tarjeta bajo control", "Bajarlo al 30 % es el siguiente paso.", 0.42, 36
    )
    assert texto.startswith("Tu meta ahora: tarjeta bajo control. Bajarlo al 30 %")
    assert "en 42 de cada 100 escenarios" in texto and "36" in texto
    assert "ningún escenario" in vc.siguiente_paso(None, None, 0.0, 36)
    assert vc.siguiente_paso(None, None, None, 0).startswith("Todavía no")


def test_mi_resumen_responde_como_voy_arriba(vistas):
    at = correr(PAGINAS / "mi_resumen.py", CLIENTE)
    assert not at.exception, [e.value for e in at.exception]
    html = "\n".join(str(m.value) for m in at.markdown)
    assert "gd-heroe" in html and "Tienes disponible" in html
    assert "&#36;30,000.00 MXN" in html  # saldo de CLI-1 en las fixtures
    for titulo in ("Gastaste este mes", "Lo que más creció", "Tu nivel de riesgo"):
        assert titulo in html, titulo
    assert "Tu siguiente paso" in html


def test_portafolio_lista_a_quien_atender(vistas):
    at = correr(PAGINAS / "portafolio.py", ADMIN)
    assert not at.exception, [e.value for e in at.exception]
    botones = [b for b in at.button if b.label == "Ver cliente"]
    assert len(botones) == 2  # las fixtures tienen dos clientes
    html = "\n".join(str(m.value) for m in at.markdown)
    # Primero el de mayor riesgo (CLI-2, 62 %), con su banda escrita.
    assert html.index("CLI-2") < html.index("CLI-1")
    assert "Riesgo alto · 62.0 %" in html


# --- Prioridad del siguiente paso y autonomia -----------------------------------


def test_autonomia_de_saldo():
    from dashboard import vista_cliente as vc

    # CLI-000283 en la instancia de la demo: saldo y capacidad de ahorro (12 m).
    meses = vc.autonomia_meses(15215.70, -27030.03)
    assert meses == pytest.approx(0.563, abs=1e-3)
    assert vc.frase_autonomia(meses, 12) == (
        "A tu ritmo de los últimos 12 meses, tu saldo alcanza para menos de 1 mes."
    )
    assert vc.texto_autonomia(1.9) == "1 mes" and vc.texto_autonomia(4.2) == "4 meses"
    assert vc.autonomia_meses(30000, 6000) is None, "sin deficit no hay autonomia"
    assert vc.frase_autonomia(None, 12) is None
    assert vc.autonomia_meses(0, -100) == 0.0


ESCALERA = ("Pagos de deuda bajo control", "Tus pagos de deuda son $25,513 al mes.")


def test_siguiente_paso_con_riesgo_alto_y_deficit():
    from dashboard import vista_cliente as vc

    argumentos = ("alto", 0.5974, 15215.70, -27030.03, 12, *ESCALERA, 1.0, 36)
    parrafos = vc.siguiente_paso_priorizado(*argumentos)
    assert parrafos[0] == (
        "Lo primero: en promedio, en los últimos 12 meses, gastas $27,030 más de lo "
        "que te entra al mes. A ese ritmo, tu saldo de $15,216 alcanza para menos de "
        "1 mes. Además, tu riesgo de atrasarte en tus pagos es alto (59.7 %)."
    )
    assert parrafos[1].startswith(
        "Tu paso en la escalera: pagos de deuda bajo control."
    )
    assert "100 de cada 100" in parrafos[2]
    assert vc.siguiente_paso_priorizado(*argumentos) == parrafos, "reproducible"


def test_siguiente_paso_deficit_sin_riesgo_alto():
    from dashboard import vista_cliente as vc

    parrafos = vc.siguiente_paso_priorizado(
        "medio", 0.2, 5000.0, -2500.0, 12, *ESCALERA, 0.3, 36
    )
    assert parrafos[0].startswith("Lo primero: en promedio")
    assert "alcanza para 2 meses" in parrafos[0] and "alto" not in parrafos[0]


def test_siguiente_paso_riesgo_alto_sin_deficit():
    from dashboard import vista_cliente as vc

    parrafos = vc.siguiente_paso_priorizado(
        "alto",
        0.41,
        50000.0,
        1200.0,
        12,
        *ESCALERA,
        0.1,
        36,
        "Usas el 90 % de tu tarjeta",
    )
    assert parrafos[0] == (
        "Lo primero: tu riesgo de atrasarte en tus pagos es alto (41.0 %). Lo que más "
        "lo sube: usas el 90 % de tu tarjeta."
    )


def test_siguiente_paso_cliente_sano_queda_igual():
    from dashboard import vista_cliente as vc

    # Riesgo bajo y sin deficit: el mismo texto de siempre.
    parrafos = vc.siguiente_paso_priorizado(
        "bajo", 0.05, 40000.0, 11313.0, 12, *ESCALERA, 0.0, 36
    )
    assert parrafos == [vc.siguiente_paso(*ESCALERA, 0.0, 36)]
    assert parrafos[0].startswith("Tu meta ahora:")
    # Deficit con saldo para 3 meses o mas: tampoco aplica la prioridad.
    holgado = vc.siguiente_paso_priorizado(
        "bajo", 0.05, 9000.0, -3000.0, 12, *ESCALERA, 0.0, 36
    )
    assert holgado == [vc.siguiente_paso(*ESCALERA, 0.0, 36)]


@pytest.mark.parametrize(
    "cliente, prioridad",
    [("CLI-2", True), ("CLI-1", False)],
    ids=["riesgo-alto", "sano"],
)
def test_mi_resumen_prioriza_segun_el_cliente(vistas, cliente, prioridad):
    at = correr(PAGINAS / "mi_resumen.py", Usuario(cliente, "cliente"))
    assert not at.exception, [e.value for e in at.exception]
    texto = "\n".join(str(m.value) for m in at.markdown).replace("\\$", "$")
    assert ("Lo primero:" in texto) is prioridad
    assert ("Tu paso en la escalera:" in texto) is prioridad
    # Autonomia en la tarjeta heroe solo con deficit (CLI-2: -$3,000 al mes).
    assert ("tu saldo alcanza para" in texto) is prioridad

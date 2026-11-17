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

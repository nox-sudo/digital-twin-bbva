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
        (ADMIN, "¿Qué porcentaje de los clientes gasta más de lo que gana?"),
    ],
    ids=["cliente", "admin", "admin-porcentaje"],
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


def test_mi_resumen_responde_como_voy_arriba(vistas):
    at = correr(PAGINAS / "mi_resumen.py", CLIENTE)
    assert not at.exception, [e.value for e in at.exception]
    html = "\n".join(str(m.value) for m in at.markdown)
    assert "gd-heroe" in html and "Tienes disponible" in html
    assert "&#36;30,000.00 MXN" in html  # saldo de CLI-1 en las fixtures
    for titulo in ("Gastaste este mes", "Lo que más creció", "Tu nivel de riesgo"):
        assert titulo in html, titulo
    assert "gd-titular" in html, "Tu siguiente paso: titular, accion y chips"


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


# Metricas de la escalera de CLI-000283 en la instancia de la demo.
METRICAS_283 = {
    "pago_comprometido": 25512.94,
    "ingreso_referencia": 36664.0,
    "razon_pagos_deuda": 0.70,
    "uso_tarjeta": 0.38,
    "gasto_mensual": 79825.0,
    "saldo_liquido": 15215.70,
    "fondo_meses": 0.2,
    "tasa_ahorro": None,
    "ingreso_ahorro": 36665.0,
}


def test_meta_de_la_escalera_con_los_umbrales_de_la_escalera():
    from dashboard import vista_cliente as vc
    from src.gold import escalera as esc

    meta = vc.meta_escalera("pagos_deuda", METRICAS_283, esc.cargar_config())
    # El mismo tope que dice la lectura de la escalera: 30 % de $36,665.
    assert meta["accion"] == "Baja tus pagos de deuda a $10,999 al mes o menos."
    assert meta["hoy"] == "Hoy pagas $25,513 al mes de deudas"
    assert meta["falta"] == "Te faltan $14,514 por bajar"
    tarjeta = vc.meta_escalera("uso_tarjeta", METRICAS_283, esc.cargar_config())
    assert tarjeta["accion"] == "Baja el uso de tu tarjeta al 30 % de su límite."
    assert "38 %" in tarjeta["hoy"]
    assert tarjeta["falta"] == "Te faltan 8 puntos para bajar al 30 %"


def test_siguiente_paso_compacto_urgente():
    from dashboard import vista_cliente as vc

    argumentos = (
        "alto",
        15215.70,
        -27030.03,
        12,
        "Pagos de deuda bajo control",
        "Baja tus pagos de deuda a $10,999 al mes o menos.",
        1.0,
        36,
    )
    paso = vc.siguiente_paso_compacto(*argumentos)
    assert paso["titular"] == "Lo urgente: cada mes sale más dinero del que te entra."
    assert paso["accion"].startswith("Baja tus pagos de deuda")
    assert paso["chips"] == [
        "Gastas $27,030 más de lo que te entra al mes (promedio de 12 meses)",
        "Quedarte sin dinero en 36 meses: prácticamente seguro",
    ]
    assert vc.siguiente_paso_compacto(*argumentos) == paso, "reproducible"


def test_siguiente_paso_compacto_cliente_sano():
    from dashboard import vista_cliente as vc

    paso = vc.siguiente_paso_compacto(
        "bajo",
        71687.87,
        11313.0,
        12,
        "Fondo de emergencia completo",
        "Junta $54,908 más para cubrir 6 meses de gastos.",
        0.04,
        36,
    )
    assert paso["titular"] == "Tu meta ahora: fondo de emergencia completo."
    assert paso["chips"] == [
        "Te quedan $11,313 al mes (promedio de 12 meses)",
        "Quedarte sin dinero en 36 meses: 4 de cada 100 escenarios",
    ]


@pytest.mark.parametrize(
    "cliente, prioridad",
    [("CLI-2", True), ("CLI-1", False)],
    ids=["riesgo-alto", "sano"],
)
def test_mi_resumen_prioriza_segun_el_cliente(vistas, cliente, prioridad):
    at = correr(PAGINAS / "mi_resumen.py", Usuario(cliente, "cliente"))
    assert not at.exception, [e.value for e in at.exception]
    html = "\n".join(str(m.value) for m in at.markdown).replace("&#36;", "$")
    titular = re.search(r"<div class='gd-titular'>([^<]+)</div>", html).group(1)
    # CLI-2 (fixtures): riesgo alto y deficit de $3,000 al mes; CLI-1, sano.
    assert titular.startswith("Lo urgente") is prioridad
    assert titular.startswith("Tu meta ahora") is (not prioridad)
    assert html.count("class='gd-chip'") <= 2
    assert "<b>" not in html and "**" not in html.split("gd-titular")[1][:400]
    # Autonomia en la tarjeta heroe solo con deficit.
    assert ("tu saldo alcanza para" in html.split("gd-heroe")[1][:600]) is prioridad


# --- Pulido de Mi resumen ---------------------------------------------------------


@pytest.mark.parametrize(
    "p, esperado",
    [
        (1.0, "es prácticamente seguro"),
        (0.996, "es prácticamente seguro"),
        (0.0, "es muy poco probable"),
        (0.004, "es muy poco probable"),
        (0.42, None),
        (0.01, None),
    ],
)
def test_certeza_en_los_extremos(p, esperado):
    from dashboard import vista_cliente as vc

    assert vc.certeza(p) == esperado


def test_riesgo_de_compra_en_palabras():
    from dashboard import vista_cliente as vc

    assert vc.frase_riesgo_compra(1.0, 1.0, 36) == (
        "Con o sin la compra, es prácticamente seguro que te quedes sin dinero "
        "algún mes de los próximos 36."
    )
    assert vc.frase_riesgo_compra(0.5, 0.0, 36) == (
        "Con la compra, en 50 de cada 100 escenarios simulados te quedarías sin "
        "dinero algún mes de los próximos 36; sin ella, es muy poco probable."
    )
    assert vc.frase_riesgo_compra(0.5, 0.3, 12).endswith("sin ella, en 30.")


def test_sin_palabras_internas_para_el_cliente():
    from dashboard import vista_cliente as vc

    texto = vc.lenguaje_cliente(
        "Pago de deuda estimado con las tasas del catálogo (tarjeta 45 %)."
    )
    assert "catálogo" not in texto and "tasas de referencia" in texto


def test_mi_resumen_saluda_y_rotula_el_uso_de_la_tarjeta(vistas):
    at = correr(PAGINAS / "mi_resumen.py", Usuario("CLI-2", "cliente"))
    assert not at.exception, [e.value for e in at.exception]
    html = "\n".join(str(m.value) for m in at.markdown)
    assert "Hola. Así van tus finanzas al cierre de" in html
    assert "Límite de tu tarjeta que usas" in html
    assert "gd-fila" in html, "las tres tarjetas van en una cuadricula"


def test_el_simulador_dice_el_conteo_exacto_solo_al_admin(vistas):
    textos_por_rol = {}
    for usuario in (ADMIN, Usuario("CLI-2", "cliente")):
        at = correr(PAGINAS / "simulador.py", usuario, cliente_id="CLI-2")
        assert not at.exception, [e.value for e in at.exception]
        textos_por_rol[usuario.rol] = "\n".join(str(m.value) for m in at.markdown)
    assert "de cada 100**" in textos_por_rol["admin"]
    assert "de cada 100**" not in textos_por_rol["cliente"]


def test_cada_cifra_vive_en_un_solo_lugar(vistas):
    """Mi resumen de CLI-2 (fixtures): ninguna cifra en pesos aparece en mas de
    una tarjeta. El deficit de 12 meses va en un chip, lo que salio en
    "Gastaste este mes" y los pagos de deuda en "Tu credito"."""
    at = correr(PAGINAS / "mi_resumen.py", Usuario("CLI-2", "cliente"))
    assert not at.exception, [e.value for e in at.exception]
    # Por elemento (cada tarjeta, frase o nota), los montos sin centavos ni
    # "MXN", para comparar "$3,000" con "$3,000.00 MXN". Dos categorias con el
    # mismo monto en una misma frase no son el mismo dato repetido.
    por_elemento = []
    for e in [*at.markdown, *at.caption]:
        t = re.sub(r"<[^>]+>", " ", str(e.value)).replace("&#36;", "$")
        t = t.replace("\\$", "$")
        montos = re.findall(r"-?\$[\d,]+(?:\.\d+)?", t)
        por_elemento.append({m.split(".")[0].rstrip(",") for m in montos})
    vistos = [m for montos in por_elemento for m in montos]
    repetidos = {m for m in vistos if vistos.count(m) > 1}
    assert repetidos == set(), repetidos


def test_comparacion_de_gasto_con_la_ventana_de_12_meses():
    from dashboard import vista_cliente as vc

    # 18 meses anteriores: 6 de 50 y 12 de 100; este mes, 110.
    flujo = pd.DataFrame(
        {
            "mes": [f"m{i:02d}" for i in range(19)],
            "salidas": [50.0] * 6 + [100.0] * 12 + [110.0],
        }
    )
    r = vc.comparacion_gasto(flujo, 12)
    assert r["promedio"] == 100.0
    assert r["frase"] == "10 % más que tu promedio de los 12 meses anteriores."
    assert vc.comparacion_gasto(flujo)["promedio"] == pytest.approx(1500 / 18)


def test_escalera_visual_con_el_paso_actual(vistas):
    at = correr(PAGINAS / "mi_resumen.py", Usuario("CLI-2", "cliente"))
    assert not at.exception, [e.value for e in at.exception]
    html = "\n".join(str(m.value) for m in at.markdown)
    escalera = re.search(r"<ol class='gd-escalera'[^>]*>(.*?)</ol>", html).group(1)
    pasos = re.findall(r"<li class='gd-escalon ?([a-z]*)'>", escalera)
    assert len(pasos) == 6
    assert pasos.count("actual") == 1
    actual = pasos.index("actual")
    assert all(p == "hecho" for p in pasos[:actual])
    assert all(p == "" for p in pasos[actual + 1 :])  # noqa: E203
    # El estado tambien va escrito, no solo en el color.
    assert "estás aquí" in escalera
    assert "Para el siguiente paso:" in html


def test_medidor_de_riesgo_junto_al_nivel(vistas):
    at = correr(PAGINAS / "mi_resumen.py", Usuario("CLI-2", "cliente"))
    assert not at.exception, [e.value for e in at.exception]
    html = "\n".join(str(m.value) for m in at.markdown)
    medidor = re.search(r"<div class='gd-medidor'.*?</div></div>", html).group(0)
    # CLI-2 (fixtures): probabilidad 0.62, banda alta; cortes en 10 % y 30 %.
    assert "width:62.0%" in medidor
    assert "left:10.0%" in medidor and "left:30.0%" in medidor
    assert "62.0 % de probabilidad en 6 meses" in medidor
    # La probabilidad vive solo en el medidor (su leyenda y su aria-label).
    assert "62.0 %" not in html.replace(medidor, "")

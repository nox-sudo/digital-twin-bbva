"""
tests/test_vista_cliente.py

Vista "Mi resumen" (dashboard/paginas/mi_resumen.py y las frases de
dashboard/vista_cliente.py):

  - Las frases de cada tarjeta, con cifras conocidas.
  - La pagina completa (AppTest) para un cliente de un salario minimo, uno
    de $20,000, uno con ingreso variable y uno sin credito: sin errores, con
    las seis tarjetas, montos en pesos y el "¿me alcanza?" funcionando.
  - Sin las tablas nuevas o sin modelo, cada tarjeta lo dice en vez de fallar.
  - Un cliente solo ve su resumen; el admin elige a quien ver.
"""

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

st_testing = pytest.importorskip("streamlit.testing.v1")

import asistente.herramientas  # noqa: E402,F401  (antes que la pagina asistente.py)
from dashboard import vista_cliente as vc  # noqa: E402
from datos_vista_cliente import CLIENTES, SALARIO_MINIMO, crear_gold_vista  # noqa: E402
from src.common.formato import formato_mxn  # noqa: E402
from src.common.usuarios import Usuario  # noqa: E402
from src.gold import explicacion as ex  # noqa: E402
from test_moneda_mxn import problemas_de_moneda  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
PAGINA = RAIZ / "dashboard" / "paginas" / "mi_resumen.py"
TARJETAS = [
    "Este mes",
    "Tus ingresos por quincena",
    "A dónde se va tu dinero",
    "Tu crédito",
    "Tu riesgo, explicado",
    "¿Me alcanza para...?",
]


def nombre_mes(mes):
    return {"2026-11": "nov 2026", "2026-12": "dic 2026"}.get(mes, mes)


# --- Frases ------------------------------------------------------------------


def test_resumen_mes():
    flujo = pd.DataFrame(
        {
            "mes": ["2026-11", "2026-12"],
            "entradas": [20000.0, 20000.0],
            "salidas": [15000.0, 23000.0],
            "neto": [5000.0, -3000.0],
        }
    )
    r = vc.resumen_mes(flujo, nombre_mes)
    assert r["neto"] == -3000 and r["promedio_neto"] == 5000
    assert r["frase"] == (
        "En dic 2026 te entraron $20,000 y salieron $23,000. Gastaste $3,000 más "
        "de lo que te entró. En un mes normal te quedan $5,000."
    )
    assert vc.resumen_mes(flujo.iloc[:0], nombre_mes) is None


def quincenas(montos, depositos=None):
    filas = [
        (f"2026-{m:02d}", q, monto)
        for m, par in enumerate(montos, start=1)
        for q, monto in zip((1, 2), par)
    ]
    df = pd.DataFrame(filas, columns=["mes", "quincena", "monto"])
    df["depositos"] = df["monto"] if depositos else 0.0
    return df


def test_quincenas_de_nomina():
    r = vc.resumen_quincenas(quincenas([(4791.24, 4791.24)] * 12), 0.5)
    assert not r["variable"]
    assert r["frase"] == "Te llegan unos $4,791 cada quincena: el 15 y a fin de mes."


def test_quincenas_de_ingreso_variable():
    r = vc.resumen_quincenas(quincenas([(3000, 5000), (0, 9000)] * 6, True), 0.5)
    assert r["variable"]
    assert "cambian de una quincena a otra" in r["frase"]
    assert "En 6 quincenas no te entró nada." in r["frase"]


def test_resumen_gastos():
    salidas = pd.DataFrame(
        {
            "categoria": ["comida", "pago_deuda", "ropa", "otros"],
            "total": [6000.0, 3000.0, 600.0, 400.0],
        }
    )
    salidas["proporcion"] = salidas["total"] / salidas["total"].sum()
    nombres = {"comida": "Comida", "pago_deuda": "Pago de deuda", "ropa": "Ropa"}
    frase = vc.resumen_gastos(salidas, nombres.get, 3)
    assert frase == (
        "En los últimos 3 meses, lo que más se llevó tu dinero fue comida ($6,000, "
        "60 %), pago de deuda ($3,000, 30 %) y ropa ($600, 6 %)."
    )


BASE_COMPRA = {
    "precio": 15000.0,
    "saldo_disponible": 1200.0,
    "meses_simulados": 36,
    "probabilidad_deficit_sin_compra": 0.3,
    "probabilidad_deficit_con_compra": 0.5,
}


@pytest.mark.parametrize(
    "extra, esperado",
    [
        (
            {
                "forma_pago": "contado",
                "alcanza_con_el_saldo_hoy": False,
                "meses_para_reunir_el_precio": 9.2,
            },
            "te faltan $13,800. Ahorrando como hasta ahora lo juntas en unos 10 meses.",
        ),
        (
            {
                "forma_pago": "contado",
                "alcanza_con_el_saldo_hoy": False,
                "meses_para_reunir_el_precio": None,
            },
            "no lo juntarías",
        ),
        (
            {
                "forma_pago": "contado",
                "alcanza_con_el_saldo_hoy": True,
                "meses_para_reunir_el_precio": 0,
            },
            "Sí te alcanza",
        ),
        (
            {
                "forma_pago": "credito",
                "cuota_mensual": 1520.4,
                "plazo_meses": 12,
                "iva_sobre_intereses": 0.16,
            },
            "Pagarías $1,520 al mes durante 12 meses, ya con el IVA de 16 %",
        ),
    ],
)
def test_resumen_compra(extra, esperado):
    frase = vc.resumen_compra({**BASE_COMPRA, **extra})
    assert esperado in frase
    if extra["forma_pago"] == "credito" or extra["alcanza_con_el_saldo_hoy"]:
        assert "en 50 de cada 100 escenarios" in frase and "sin ella, en 30" in frase


def test_resumen_compra_con_error():
    assert vc.resumen_compra({"error": "precio invalido"}) == "precio invalido"


# --- Pagina completa -----------------------------------------------------------


@pytest.fixture
def vista(tmp_path, monkeypatch):
    import streamlit as st

    from dashboard import comun

    cfg = crear_gold_vista(tmp_path)
    monkeypatch.setattr(comun, "config", lambda: cfg)
    st.cache_data.clear()
    return cfg


def correr(usuario: Usuario, **estado):
    at = st_testing.AppTest.from_file(str(PAGINA), default_timeout=120)
    at.session_state["usuario"] = usuario
    for llave, valor in estado.items():
        at.session_state[llave] = valor
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def textos(at) -> list[str]:
    elementos = [*at.title, *at.subheader, *at.markdown, *at.caption, *at.warning]
    # Las cifras grandes van en HTML con el signo de pesos como entidad.
    salida = [str(e.value).replace("&#36;", "$") for e in elementos]
    return salida


def selector_de_cliente(at) -> list:
    return [s for s in at.selectbox if s.label == "Cliente"]


def todo(at) -> str:
    return "\n".join(textos(at)).replace("\\$", "$")


@pytest.mark.parametrize("cliente", CLIENTES)
def test_la_vista_completa_para_cada_cliente(vista, cliente):
    at = correr(Usuario(cliente, "cliente"))
    assert [s.value for s in at.subheader] == TARJETAS
    assert at.title[0].value == "Mi resumen"
    assert not selector_de_cliente(at)
    texto = todo(at)
    assert "estará disponible" not in texto
    assert "Lo que más lo sube" in texto and "Lo que más lo baja" in texto
    for t in textos(at):
        assert problemas_de_moneda(t) == [], t


def test_un_salario_minimo(vista):
    at = correr(Usuario("CLI-SM", "cliente"))
    texto = todo(at)
    quincena = formato_mxn(SALARIO_MINIMO / 2, estilo="cliente")
    assert f"Te llegan unos {quincena} cada quincena" in texto
    assert "Paso 1 de 6" not in texto  # sin deuda, no se queda en el paso 1
    assert "Sin tarjeta" in texto

    # 15,000 de contado: con $1,200 de saldo no alcanza.
    at.number_input[0].set_value(15000)
    at.button[0].click().run()
    assert "Hoy no te alcanza: te faltan $13,800." in todo(at)


def test_ingreso_de_20_mil(vista):
    at = correr(Usuario("CLI-20K", "cliente"))
    texto = todo(at)
    assert "Te llegan unos $10,000 cada quincena" in texto
    assert "Pagas de deudas al mes" in texto

    at.number_input[0].set_value(15000)
    at.button[0].click().run()
    assert "Sí te alcanza: tienes $25,000 y cuesta $15,000." in todo(at)

    # A credito sin tasa: la pide en vez de suponerla.
    at.radio[0].set_value("A crédito")
    at.button[0].click().run()
    assert "Escribe la tasa" in at.warning[0].value
    at.number_input[1].set_value(40.0)
    at.button[0].click().run()
    assert "Pagarías" in todo(at) and "IVA de 16 %" in todo(at)


def test_ingreso_variable(vista):
    texto = todo(correr(Usuario("CLI-VAR", "cliente")))
    assert "Tus ingresos cambian de una quincena a otra" in texto
    assert "no te entró nada" in texto


def test_sin_credito(vista):
    texto = todo(correr(Usuario("CLI-SIN", "cliente")))
    assert "Sin tarjeta" in texto
    assert "$0" in texto  # pago de deudas


def test_los_factores_son_los_shap_del_cliente(vista):
    at = correr(Usuario("CLI-20K", "cliente"))
    e = ex.explicar_cliente(
        vista["gold_duckdb"],
        vista["modelo"],
        vista["probabilidad_oof"],
        vista["modelos_pliegues"],
        "CLI-20K",
    )
    n = ex.cargar_config()["factores_mostrados"]
    mostrados = [t[2:] for t in textos(at) if t.startswith("- ")]
    assert [m.replace("\\$", "$") for m in mostrados] == [
        f.frase for f in e.principales(n) if f.sube
    ] + [f.frase for f in e.principales(n) if not f.sube]


def test_sin_tablas_nuevas_ni_modelo(vista):
    con = duckdb.connect(vista["gold_duckdb"])
    con.execute("DROP TABLE gold_ingresos_quincenales")
    con.execute("DROP TABLE gold_creditos_cliente")
    con.close()
    Path(vista["modelo"]).unlink()
    at = correr(Usuario("CLI-20K", "cliente"))
    texto = todo(at)
    assert "El detalle por quincena estará disponible" in texto
    assert "Tu crédito estará disponible" in texto
    assert "Por qué: Todavía no hay un modelo entrenado." in texto
    assert [s.value for s in at.subheader] == TARJETAS


def test_un_cliente_solo_ve_su_resumen(vista):
    admin = correr(Usuario("admin", "admin"), cliente_id="CLI-SM")
    assert admin.title[0].value == "Vista del cliente"
    assert selector_de_cliente(admin)[0].value == "CLI-SM"
    referencia = [m.value for m in admin.markdown]
    assert any("$" in m for m in referencia)

    at = st_testing.AppTest.from_file(str(PAGINA), default_timeout=120)
    at.session_state["usuario"] = Usuario("CLI-SM", "cliente")
    at.session_state["cliente_id"] = "CLI-20K"
    at.query_params["cliente_id"] = "CLI-20K"
    at.run()
    assert not at.exception
    assert not selector_de_cliente(at)
    assert [m.value for m in at.markdown] == referencia
    assert "CLI-20K" not in todo(at)


def test_la_vista_pide_sesion(vista):
    at = st_testing.AppTest.from_file(str(PAGINA), default_timeout=60)
    at.run()
    assert "No tienes acceso" in at.error[0].value
    assert not at.metric and not at.markdown

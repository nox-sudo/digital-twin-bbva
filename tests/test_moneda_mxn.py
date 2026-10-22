"""
tests/test_moneda_mxn.py

Todo monto que ve una persona sale en pesos (MXN) con formato_mxn, y nada en
dolares. Revisa las vistas del dashboard (AppTest de Streamlit, sobre el Gold
minimo de las fixtures, nunca sobre data/gold), las lecturas de todas las
herramientas del asistente y la escalera financiera.

Dos reglas, definidas de forma estrecha a proposito:

  - Unidades de otra moneda junto a un monto: "USD", "US$" o "$... dolares".
    La palabra "dolar" sola no cuenta: el filtro de alcance puede rechazar
    "Compro dolares ahora?" y su respuesta puede repetirla.
  - Todo lo que lleve "$" coincide con lo que produce formato_mxn: "$20,407",
    "-$4,091" o "$20,407 MXN". Porcentajes, conteos, anios o plazos no llevan
    "$" y no se revisan.
"""

import re
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asistente import herramientas as h  # noqa: E402
from asistente.alcance import RESPUESTA_INVERSION  # noqa: E402
from datos_asistente import contexto, crear_gold  # noqa: E402
from src.common.formato import formato_mxn  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]

# Lo que produce formato_mxn (0 decimales por defecto).
# El monto termina donde termina el formato: "$8360.73" no pasa.
MONTO_MXN = re.compile(r"-?\$\d{1,3}(?:,\d{3})*(?:\.\d+)?(?: MXN)?(?![\d,])")
OTRA_MONEDA = [
    re.compile(r"\bUSD\b"),
    re.compile(r"US\$"),
    re.compile(r"\$\s?[\d.,]+\s*d[oó]lar", re.IGNORECASE),
    re.compile(r"\b[\d.,]+\s*d[oó]lares\b", re.IGNORECASE),
]


def problemas_de_moneda(texto: str) -> list[str]:
    """Montos fuera de formato o en otra moneda dentro de un texto."""
    texto = texto.replace("\\$", "$")  # escape de Markdown en Streamlit
    problemas = [m.group(0) for p in OTRA_MONEDA for m in p.finditer(texto)]
    for i in [m.start() for m in re.finditer(r"\$", texto)]:
        # Un "-" justo antes es el signo del monto.
        inicio = i - 1 if i > 0 and texto[i - 1] == "-" else i
        if not MONTO_MXN.match(texto, inicio):
            desde, hasta = max(0, i - 15), i + 15
            problemas.append(texto[desde:hasta])
    return problemas


def textos(valor) -> list[str]:
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, dict):
        return [t for v in valor.values() for t in textos(v)]
    if isinstance(valor, list):
        return [t for v in valor for t in textos(v)]
    return []


# --- La regla misma -----------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        f"Cuota de {formato_mxn(8360.73, estilo='cliente')} al mes",
        f"Ingreso {formato_mxn(20407)} y saldo {formato_mxn(-4091)}",
        "Riesgo de 18.3 % a 15 años en 36 meses",
        "No doy recomendaciones de inversión. ¿Compro dólares ahora?",
        "Enganche y gastos (\\$135,000): no los reuniría.",
    ],
)
def test_textos_validos(texto):
    assert problemas_de_moneda(texto) == []


@pytest.mark.parametrize(
    "texto",
    [
        "Cuota de USD 400",
        "Te cuesta US$1,200",
        "Son $500 dólares",
        "Ganas 1,000 dólares",
        "Cuota de $8360.73",
        "Tienes $ 1,000",
    ],
)
def test_textos_invalidos(texto):
    assert problemas_de_moneda(texto)


# --- Asistente y escalera -----------------------------------------------------

LLAMADAS = [
    ("perfil_cliente", {}),
    ("gasto_por_categoria", {}),
    ("gasto_por_categoria", {"categoria": "comida", "ultimos_meses": 3}),
    ("flujo_mensual", {"ultimos_meses": 3}),
    ("comparar_con_portafolio", {}),
    ("simular_hipoteca", {"precio": 900000}),
    ("evaluar_compra", {"precio": 25000, "forma_pago": "contado"}),
    ("evaluar_compra", {"precio": 90000, "forma_pago": "contado"}),
    (
        "evaluar_compra",
        {
            "precio": 450000,
            "forma_pago": "credito",
            "plazo_meses": 48,
            "tasa_anual": 0.14,
        },
    ),
    ("escalera_financiera", {}),
    ("resumen_portafolio", {}),
]


@pytest.fixture
def gold(tmp_path):
    return crear_gold(tmp_path)


def test_cubre_todas_las_herramientas():
    assert {n for n, _ in LLAMADAS} == set(h.HERRAMIENTAS)


@pytest.mark.parametrize("cliente", ["CLI-1", "CLI-2"])
def test_lecturas_del_asistente_en_pesos(gold, tmp_path, cliente):
    ctx = contexto(gold, tmp_path, cliente)
    for nombre, argumentos in LLAMADAS:
        for texto in textos(h.ejecutar(nombre, argumentos, ctx)):
            assert problemas_de_moneda(texto) == [], (nombre, texto)


def test_respuesta_fija_de_alcance():
    assert problemas_de_moneda(RESPUESTA_INVERSION) == []


# --- Vistas del dashboard (AppTest) ------------------------------------------

st_testing = pytest.importorskip("streamlit.testing.v1")


def gold_para_vistas(tmp_path) -> dict:
    """El Gold de las fixtures con las columnas que piden las vistas, y una
    configuracion que apunta solo a tmp_path."""
    ruta = crear_gold(tmp_path)
    con = duckdb.connect(ruta)
    for columna, tipo, valor in [
        ("numero_productos", "BIGINT", 1),
        ("gasto_promedio_3_meses", "DOUBLE", 12000.0),
        ("frecuencia_transacciones", "DOUBLE", 30.0),
        ("actividad_p2p", "DOUBLE", 0.0),
        ("ingreso_declarado", "DOUBLE", 18000.0),
        ("proporcion_retiros", "DOUBLE", 0.1),
    ]:
        con.execute(
            f"ALTER TABLE gold_features_cliente ADD COLUMN {columna} {tipo} DEFAULT {valor}"
        )
    con.close()
    for carpeta in ("logs", "deriva_psi", "resumen"):
        (tmp_path / carpeta).mkdir(exist_ok=True)
    return {
        "gold_duckdb": ruta,
        "logs": str(tmp_path / "logs"),
        "deriva_psi": str(tmp_path / "deriva_psi"),
        "resumen_portafolio": str(tmp_path / "resumen"),
        "monitoreo_psi": str(RAIZ / "config" / "monitoreo_psi.yaml"),
        "bandas_riesgo": [
            {"nombre": "bajo", "desde": 0.0},
            {"nombre": "medio", "desde": 0.10},
            {"nombre": "alto", "desde": 0.30},
        ],
        "ventana_meses": 12,
    }


def textos_de_la_vista(at) -> list[str]:
    elementos = [
        *at.title,
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
    return salida


@pytest.mark.parametrize(
    "pagina, cliente",
    [
        ("portafolio.py", None),
        ("cliente.py", "CLI-2"),
        ("simulador.py", "CLI-1"),
        ("simulador.py", "CLI-2"),
        ("observabilidad.py", None),
        ("asistente.py", "CLI-1"),
    ],
)
def test_vistas_del_dashboard_en_pesos(tmp_path, monkeypatch, pagina, cliente):
    import streamlit as st

    from dashboard import comun

    cfg = gold_para_vistas(tmp_path)
    monkeypatch.setattr(comun, "config", lambda: cfg)
    st.cache_data.clear()
    at = st_testing.AppTest.from_file(str(RAIZ / "dashboard" / "paginas" / pagina))
    if cliente:
        at.session_state["cliente_id"] = cliente
    at.run(timeout=60)
    assert not at.exception, [e.value for e in at.exception]
    vistos = textos_de_la_vista(at)
    assert vistos, "la vista no mostro texto"
    for texto in vistos:
        assert problemas_de_moneda(texto) == [], (pagina, texto)

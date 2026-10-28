"""
tests/test_asistente_conversacion.py

Bucle de conversacion del asistente (asistente/conversacion.py) con un modelo
falso: el CI no necesita Ollama. Comprueba el orden de los mensajes, que las
herramientas se ejecutan con el cliente del contexto, la verificacion previa
de alcance y los cortes (limite de vueltas, respuesta vacia).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asistente import conversacion as c  # noqa: E402
from datos_asistente import contexto, crear_gold  # noqa: E402


@pytest.fixture
def gold(tmp_path):
    return crear_gold(tmp_path)


class ModeloFalso:
    """Devuelve las respuestas en orden y guarda los mensajes que recibio."""

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.recibidos = []

    def invoke(self, mensajes):
        self.recibidos.append([dict(m) for m in mensajes])
        if not self.respuestas:
            raise AssertionError("el modelo se llamo mas veces de lo esperado")
        return self.respuestas.pop(0)


def llamada(nombre, argumentos=None, id_="1"):
    return {
        "content": "",
        "tool_calls": [{"name": nombre, "args": argumentos or {}, "id": id_}],
    }


def texto(contenido):
    return {"content": contenido, "tool_calls": []}


def test_herramienta_y_respuesta(gold, tmp_path):
    modelo = ModeloFalso(llamada("perfil_cliente"), texto("Tu riesgo es alto."))
    r = c.responder(
        "¿Cual es mi riesgo?", [], contexto(gold, tmp_path, "CLI-2"), modelo, "2027-02"
    )
    assert (r.texto, r.motivo) == ("Tu riesgo es alto.", "respuesta")
    assert [p.herramienta for p in r.pasos] == ["perfil_cliente"]
    assert r.pasos[0].resultado["cliente_id"] == "CLI-2"
    segunda = modelo.recibidos[1]
    assert [m["role"] for m in segunda] == ["system", "user", "assistant", "tool"]
    assert segunda[2]["tool_calls"][0]["name"] == "perfil_cliente"
    assert segunda[3]["tool_call_id"] == "1"
    assert json.loads(segunda[3]["content"])["banda_riesgo"] == "alto"


def test_el_error_vuelve_al_modelo_y_puede_corregir(gold, tmp_path):
    modelo = ModeloFalso(
        llamada("perfil_cliente", {"variables": "riesgo"}),
        llamada("perfil_cliente", {}, id_="2"),
        texto("Listo."),
    )
    r = c.responder("riesgo", [], contexto(gold, tmp_path), modelo, "2027-02")
    assert r.motivo == "respuesta"
    assert "error" in r.pasos[0].resultado and "error" not in r.pasos[1].resultado
    assert "Permitidos" in modelo.recibidos[1][-1]["content"]


@pytest.mark.parametrize(
    "pregunta, contiene",
    [
        ("¿Qué acciones me recomiendas comprar?", "inversión"),
        ("¿Cuánto es mi ingreso en dólares?", "pesos mexicanos"),
        # contexto() es de CLI-1: otro id se detiene con su respuesta fija.
        ("¿Cuánto gana CLI-000005?", "CLI-1"),
    ],
)
def test_fuera_de_alcance_no_llama_al_modelo(gold, tmp_path, pregunta, contiene):
    modelo = ModeloFalso()
    r = c.responder(pregunta, [], contexto(gold, tmp_path), modelo, "2027-02")
    assert r.motivo == "fuera_de_alcance"
    assert contiene in r.texto
    assert modelo.recibidos == []


def test_limite_de_vueltas(gold, tmp_path):
    modelo = ModeloFalso(*[llamada("perfil_cliente", id_=str(i)) for i in range(3)])
    r = c.responder(
        "riesgo", [], contexto(gold, tmp_path), modelo, "2027-02", max_vueltas=3
    )
    assert (r.texto, r.motivo) == (c.LIMITE_VUELTAS, "limite_vueltas")
    assert len(r.pasos) == 3


def test_respuesta_vacia(gold, tmp_path):
    r = c.responder(
        "hola", [], contexto(gold, tmp_path), ModeloFalso(texto("  ")), "2027-02"
    )
    assert (r.texto, r.motivo) == (c.SIN_RESPUESTA, "sin_texto")


def test_historial_solo_texto_y_recortado(gold, tmp_path):
    historial = [(f"pregunta {i}", f"respuesta {i}") for i in range(5)]
    modelo = ModeloFalso(texto("ok"))
    c.responder(
        "nueva",
        historial,
        contexto(gold, tmp_path),
        modelo,
        "2027-02",
        historial_turnos=2,
    )
    mensajes = modelo.recibidos[0]
    assert [m["content"] for m in mensajes[1:]] == [
        "pregunta 3",
        "respuesta 3",
        "pregunta 4",
        "respuesta 4",
        "nueva",
    ]
    assert all("tool_calls" not in m for m in mensajes)


def test_prompt_fija_cliente_mes_y_reglas(gold, tmp_path):
    sistema = c.prompt_sistema(contexto(gold, tmp_path, "CLI-2"), "2027-02")
    assert "CLI-2" in sistema and "2027-02" in sistema
    assert "No atribuyas causas" in sistema
    assert "Toda cifra sale de una herramienta" in sistema
    assert "no lo inventes ni uses un valor tipico" in sistema
    assert "evaluar_compra" in sistema


def test_error_de_conexion_se_propaga(gold, tmp_path):
    class Apagado:
        def invoke(self, mensajes):
            raise c.ErrorConexion("Ollama no responde")

    with pytest.raises(c.ErrorConexion):
        c.responder("riesgo", [], contexto(gold, tmp_path), Apagado(), "2027-02")


def test_configuracion_del_repo():
    cfg = c.Configuracion.desde_yaml()
    assert cfg.modelo == "llama3.1:8b"
    assert (cfg.temperatura, cfg.semilla) == (0.0, 42)
    assert cfg.max_vueltas >= 1 and cfg.historial_turnos >= 0


def test_la_guarda_de_origen_no_ejecuta_y_avisa_al_modelo(gold, tmp_path):
    modelo = ModeloFalso(
        llamada("evaluar_compra", {"precio": 2500000, "forma_pago": "contado"}),
        texto("¿Cuál es el precio del auto?"),
    )
    r = c.responder(
        "quiero un bmw, ¿puedo?", [], contexto(gold, tmp_path), modelo, "2027-02"
    )
    assert r.texto == "¿Cuál es el precio del auto?"
    assert "no aparece en lo que escribio el usuario" in r.pasos[0].resultado["error"]
    assert "pregunta" in modelo.recibidos[1][-1]["content"]


def test_el_historial_cuenta_como_dato_del_usuario(gold, tmp_path):
    modelo = ModeloFalso(
        llamada("evaluar_compra", {"precio": 25000, "forma_pago": "contado"}),
        texto("Te alcanza."),
    )
    r = c.responder(
        "¿y de contado?",
        [("quiero un viaje de 25 mil", "¿De contado o a crédito?")],
        contexto(gold, tmp_path),
        modelo,
        "2027-02",
    )
    assert "error" not in r.pasos[0].resultado
    assert r.pasos[0].resultado["alcanza_con_el_saldo_hoy"] is True


def test_ajustes_de_origen_llegan_en_el_resultado(gold, tmp_path):
    modelo = ModeloFalso(
        llamada(
            "simular_hipoteca",
            {"precio": 900000, "tasa_anual": 0.12, "enganche_pct": 0.05},
        ),
        texto("ok"),
    )
    r = c.responder(
        "¿Puedo pagar una casa de 900 mil?",
        [],
        contexto(gold, tmp_path),
        modelo,
        "2027-02",
    )
    paso = r.pasos[0]
    assert paso.argumentos == {"precio": 900000}
    assert paso.resultado["tasa_anual"] == 0.11  # la de la configuracion
    assert len(paso.resultado["ajustes"]) == 2
    assert "ajustes" in json.loads(modelo.recibidos[1][-1]["content"])


# --- Llamadas escritas como texto (evaluacion v2) ------------------------------

JSON_DEPARTAMENTO = (
    '{"name": "evaluar_compra", "parameters": {"precio": 2500000, "forma_pago": '
    '"credito", "plazo_meses": 25, "tasa_anual": None}}'
)


def test_llamada_en_texto_tal_como_la_escribio_el_modelo():
    llamada_ = c.llamada_en_texto(JSON_DEPARTAMENTO)
    assert llamada_["name"] == "evaluar_compra"
    assert llamada_["args"] == {
        "precio": 2500000,
        "forma_pago": "credito",
        "plazo_meses": 25,
        "tasa_anual": None,
    }


@pytest.mark.parametrize(
    "contenido, nombre",
    [
        (
            '```json\n{"name": "perfil_cliente", "parameters": {}}\n```',
            "perfil_cliente",
        ),
        (
            '{"name": "flujo_mensual", "arguments": {"ultimos_meses": 3}}',
            "flujo_mensual",
        ),
        ('{"name": "no_existe", "parameters": {}}', None),
        ('Tu gasto fue de $300. {"name": "perfil_cliente", "parameters": {}}', None),
        ("Tu riesgo es bajo.", None),
        ('{"name": "perfil_cliente", "parameters": [1, 2]}', None),
        ("{no es json}", None),
    ],
)
def test_que_cuenta_como_llamada_en_texto(contenido, nombre):
    llamada_ = c.llamada_en_texto(contenido)
    assert (llamada_["name"] if llamada_ else None) == nombre


def test_la_llamada_en_texto_pasa_por_las_guardas(gold, tmp_path):
    # Vivienda pedida como compra general: la guarda la manda a la hipoteca,
    # igual que si la llamada hubiera llegado como tool_call.
    modelo = ModeloFalso(texto(JSON_DEPARTAMENTO), texto("Listo."))
    r = c.responder(
        "¿Podría con un departamento de 2.5 millones a 25 años?",
        [],
        contexto(gold, tmp_path),
        modelo,
        "2027-02",
    )
    assert r.texto == "Listo."
    assert [p.herramienta for p in r.pasos] == ["simular_hipoteca"]
    assert "plazo_meses" not in r.pasos[0].argumentos
    # Al modelo le vuelve como llamada con su resultado, no como texto.
    segunda = modelo.recibidos[1]
    assert segunda[-2]["tool_calls"][0]["name"] == "evaluar_compra"
    assert segunda[-1]["role"] == "tool"


# --- Ids de otros clientes en la respuesta (evaluacion v2) ------------------------


def test_respuesta_con_clientes_inventados_se_cambia(gold, tmp_path):
    lista = (
        "Los 5 clientes con más riesgo son:\n1. CLI-000123 con 23.1 %\n"
        "2. CLI-000456 con 21.9 %"
    )
    modelo = ModeloFalso(llamada("resumen_portafolio"), texto(lista))
    r = c.responder(
        "Lista los clientes con más riesgo",
        [],
        contexto(gold, tmp_path),
        modelo,
        "2027-02",
    )
    assert r.motivo == "fuera_de_alcance"
    assert "CLI-000123" not in r.texto and "CLI-1" in r.texto
    assert [p.herramienta for p in r.pasos] == ["resumen_portafolio"]


def test_el_id_propio_en_la_respuesta_pasa(gold, tmp_path):
    modelo = ModeloFalso(texto("CLI-1, tu riesgo es bajo."))
    r = c.responder("¿Mi riesgo?", [], contexto(gold, tmp_path), modelo, "2027-02")
    assert r.motivo == "respuesta" and r.texto == "CLI-1, tu riesgo es bajo."


def test_menciona_otro_cliente():
    assert c.menciona_otro_cliente("Hola cli-000005", "CLI-000001")
    assert not c.menciona_otro_cliente("Hola CLI-000001", "CLI-000001")
    assert not c.menciona_otro_cliente("Sin ids", "CLI-000001")

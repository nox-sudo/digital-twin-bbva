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


def test_fuera_de_alcance_no_llama_al_modelo(gold, tmp_path):
    modelo = ModeloFalso()
    r = c.responder(
        "¿Qué acciones me recomiendas comprar?",
        [],
        contexto(gold, tmp_path),
        modelo,
        "2027-02",
    )
    assert r.motivo == "fuera_de_alcance"
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

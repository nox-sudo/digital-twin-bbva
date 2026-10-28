"""
asistente/conversacion.py

Bucle de conversacion del asistente: una pregunta entra, el modelo pide
herramientas (asistente/herramientas.py), Python las ejecuta y el modelo
redacta la respuesta con lo que devolvieron.

  1. Verificacion previa de alcance (asistente/alcance.py): una pregunta
     fuera de alcance se contesta con un texto fijo, sin llamar al modelo.
  2. Mensajes: prompt de sistema, las ultimas preguntas y respuestas (solo
     texto) y la pregunta nueva.
  3. Hasta max_vueltas rondas: si el modelo pide herramientas se ejecutan
     con ejecutar() (el cliente sale del contexto, nunca del modelo) y sus
     resultados vuelven como mensajes "tool"; si contesta sin pedir nada,
     esa es la respuesta. Antes de ejecutar, asistente/origen.py descarta o
     rechaza datos que el usuario no dio (precio, plazo, tasa...) y redirige
     una vivienda evaluada como compra general a la hipoteca.
     Si el modelo escribe la llamada como texto (un JSON con el nombre de
     una herramienta) en vez de hacerla, se trata como llamada: asi pasa por
     las mismas guardas y la persona no ve el JSON.
  4. Revision de la respuesta final: si nombra otro cliente_id se cambia
     por la respuesta fija; si dice que no hubo movimientos cuando la
     herramienta si los trajo, se cambia por la lectura de ese resultado.

El bucle no depende de LangChain: recibe cualquier objeto con
invoke(mensajes) -> {"content": str, "tool_calls": [...]}, asi que las
pruebas usan un modelo falso y el CI no necesita Ollama. ModeloOllama es el
adaptador real (grupo de dependencias "asistente").
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from asistente.alcance import RESPUESTA_OTRO_CLIENTE, verificar_alcance
from asistente.herramientas import RAIZ, Contexto, ejecutar, esquemas
from asistente.origen import revisar_llamada
from src.common.formato import formato_mxn

log = logging.getLogger(__name__)

# Un cliente_id en la respuesta final. Si es de otro cliente, el modelo lo
# invento: ninguna herramienta devuelve datos de otros clientes.
_ID_CLIENTE = re.compile(r"\bCLI-\d{6}\b", re.IGNORECASE)

# "No hubo movimientos", "no hay datos": la respuesta niega que haya datos.
_NIEGA_DATOS = re.compile(
    r"\bno (hubo|hay|tuv\w+|se registr\w+|registr\w+|encontr\w+) "
    r"(ningun\w* )?(movimientos?|informacion|datos|gastos?)\b"
)

# Bloque ```json ... ``` alrededor de una llamada escrita como texto.
_CERCA = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
# Valores de Python que el modelo escribe dentro del JSON.
_PYTHON_A_JSON = [(r"\bNone\b", "null"), (r"\bTrue\b", "true"), (r"\bFalse\b", "false")]

SIN_RESPUESTA = (
    "No pude armar una respuesta con los datos disponibles. Intenta con una "
    "pregunta mas concreta (un mes, una categoria o un monto)."
)
LIMITE_VUELTAS = (
    "La pregunta necesito mas consultas de las que permito en una respuesta. "
    "Intenta dividirla en preguntas mas cortas."
)


class ErrorConexion(RuntimeError):
    """El modelo local no responde (Ollama apagado o modelo sin descargar)."""


@dataclass(frozen=True)
class Configuracion:
    url: str
    modelo: str
    temperatura: float
    semilla: int
    max_vueltas: int
    historial_turnos: int

    @classmethod
    def desde_yaml(cls, ruta: Path = RAIZ / "config" / "asistente.yaml"):
        with open(ruta, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cls(
            url=cfg["ollama"]["url"],
            modelo=cfg["ollama"]["modelo"],
            temperatura=float(cfg["ollama"]["temperatura"]),
            semilla=int(cfg["ollama"]["semilla"]),
            max_vueltas=int(cfg["conversacion"]["max_vueltas"]),
            historial_turnos=int(cfg["conversacion"]["historial_turnos"]),
        )


@dataclass
class Paso:
    """Una herramienta que se ejecuto para responder."""

    herramienta: str
    argumentos: dict
    resultado: dict


@dataclass
class Respuesta:
    texto: str
    # respuesta | fuera_de_alcance | limite_vueltas | sin_texto
    motivo: str
    pasos: list[Paso] = field(default_factory=list)


def prompt_sistema(ctx: Contexto, ultimo_mes: str) -> str:
    return (
        "Eres el asistente del Gemelo Digital Financiero. Respondes en espanol, "
        f"en pocas frases, sobre el cliente {ctx.cliente_id} y sobre el "
        "portafolio de clientes. El ultimo mes con datos es "
        f"{ultimo_mes}.\n\n"
        "Reglas:\n"
        "- Toda cifra sale de una herramienta. Nunca inventes, calcules ni "
        "redondees de memoria un numero que una herramienta no devolvio.\n"
        "- Usa solo los argumentos que el usuario dio; omite los demas.\n"
        "- Si a la pregunta le falta un dato que ninguna herramienta tiene (por "
        "ejemplo el precio de lo que quiere comprar, o el plazo y la tasa de un "
        "credito), no lo inventes ni uses un valor tipico: pregunta por ese dato "
        "en una frase y di para que lo necesitas.\n"
        "- Una casa, departamento o terreno va siempre con simular_hipoteca, "
        "aunque la pregunta diga comprar; cualquier otra compra (un auto, un "
        "viaje) va con evaluar_compra.\n"
        "- En una compra o un credito no concluyas solo 'puedes' o 'no puedes': "
        "da la cuota o el saldo y como cambia la probabilidad de quedar en saldo "
        "negativo; si con la compra es alta, dilo claramente.\n"
        "- Si ninguna herramienta cubre la pregunta, dilo y menciona que si "
        "puedes responder; no contestes solo que no puedes ayudar.\n"
        "- Si una herramienta devuelve sin_movimientos, la respuesta es que no "
        "hubo movimientos (0), no un error.\n"
        "- Si una herramienta devuelve error, vuelve a llamarla corrigiendo los "
        "argumentos con los valores validos que trae el mensaje. Si no se puede "
        "corregir, explica en una frase que dato falta; no repitas el mensaje "
        "tecnico.\n"
        "- Las herramientas sin argumentos (perfil_cliente, resumen_portafolio) "
        "se llaman sin argumentos.\n"
        "- No atribuyas causas a la probabilidad de impago: es la salida de un "
        "modelo de riesgo y las herramientas no dicen por que tiene ese valor. "
        "Puedes mencionar las cifras del cliente que la acompanan.\n"
        "- Los montos son pesos mexicanos; las proporciones y probabilidades "
        "dilas como porcentaje.\n"
        "- Los datos son sinteticos, de un gemelo digital; no son de una persona "
        "real."
    )


def _mensajes_iniciales(
    pregunta: str, historial: list[tuple[str, str]], sistema: str, turnos: int
) -> list[dict]:
    mensajes = [{"role": "system", "content": sistema}]
    for anterior, respuesta in historial[-turnos:] if turnos else []:
        mensajes.append({"role": "user", "content": anterior})
        mensajes.append({"role": "assistant", "content": respuesta})
    mensajes.append({"role": "user", "content": pregunta})
    return mensajes


def menciona_otro_cliente(texto: str, cliente_id: str) -> bool:
    """La respuesta nombra un cliente_id distinto al de la conversacion. En la
    evaluacion v2 el modelo invento una lista de cinco clientes con su
    probabilidad; la respuesta se cambia por la fija de otro cliente."""
    ids = {i.upper() for i in _ID_CLIENTE.findall(texto)}
    return bool(ids - {cliente_id.upper()})


def _sin_acentos(texto: str) -> str:
    normal = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in normal if not unicodedata.combining(c))


def _pesos(valor) -> str:
    return formato_mxn(valor, estilo="cliente")


def lectura(herramienta: str, resultado: dict) -> str | None:
    """Una frase con las cifras de un resultado con movimientos, para
    reemplazar una respuesta que los niega."""
    if herramienta == "flujo_mensual" and resultado.get("meses"):
        meses = resultado["meses"]
        if len(meses) == 1:
            m = meses[0]
            return (
                f"En {m['mes']} entraron {_pesos(m['entradas'])} y salieron "
                f"{_pesos(m['salidas'])}: neto de {_pesos(m['neto'])}."
            )
        return (
            f"De {resultado['desde']} a {resultado['hasta']} el neto promedio fue "
            f"{_pesos(resultado['neto_promedio'])} al mes; "
            f"{resultado['meses_con_neto_negativo']} de {len(meses)} meses "
            "salieron mas de lo que entro."
        )
    if herramienta == "gasto_por_categoria":
        if resultado.get("lectura"):
            return resultado["lectura"]
        if resultado.get("categorias"):
            mayor = resultado["categorias"][0]
            return (
                f"Salidas de {resultado['desde']} a {resultado['hasta']}: "
                f"{_pesos(resultado['total_salidas'])}; la mayor categoria fue "
                f"{mayor['categoria']} con {_pesos(mayor['total'])}."
            )
    return None


def niega_datos_que_hay(texto: str, pasos: list[Paso]) -> str | None:
    """Si la respuesta dice que no hubo movimientos y la ultima herramienta
    con movimientos si los trajo, la lectura de ese resultado; si no, None.
    En la evaluacion v4 el modelo contesto "No hubo movimientos" con un mes
    de neto positivo en el resultado."""
    if not _NIEGA_DATOS.search(_sin_acentos(texto)):
        return None
    for paso in reversed(pasos):
        if paso.resultado.get("sin_movimientos") is False:
            return lectura(paso.herramienta, paso.resultado)
    return None


def llamada_en_texto(texto: str) -> dict | None:
    """La llamada a herramienta que el modelo escribio como texto, o None.
    Solo cuenta si todo el texto es un objeto JSON con el nombre de una
    herramienta y sus argumentos ("parameters" o "arguments")."""
    texto = (texto or "").strip()
    cerca = _CERCA.match(texto)
    if cerca:
        texto = cerca.group(1)
    if not (texto.startswith("{") and texto.endswith("}")):
        return None
    for patron, reemplazo in _PYTHON_A_JSON:
        texto = re.sub(patron, reemplazo, texto)
    try:
        objeto = json.loads(texto)
    except json.JSONDecodeError:
        return None
    nombres = {e["function"]["name"] for e in esquemas()}
    if not isinstance(objeto, dict) or objeto.get("name") not in nombres:
        return None
    argumentos = objeto.get("parameters", objeto.get("arguments", {}))
    if not isinstance(argumentos, dict):
        return None
    return {"name": objeto["name"], "args": argumentos, "id": "texto"}


def responder(
    pregunta: str,
    historial: list[tuple[str, str]],
    ctx: Contexto,
    modelo,
    ultimo_mes: str,
    max_vueltas: int = 4,
    historial_turnos: int = 6,
) -> Respuesta:
    """Respuesta a una pregunta sobre el cliente del contexto. historial son
    las preguntas y respuestas anteriores, de la mas antigua a la mas
    reciente. Un ErrorConexion del modelo se propaga: lo maneja la
    interfaz."""
    fija = verificar_alcance(pregunta, ctx.cliente_id)
    if fija is not None:
        return Respuesta(texto=fija, motivo="fuera_de_alcance")

    mensajes = _mensajes_iniciales(
        pregunta, historial, prompt_sistema(ctx, ultimo_mes), historial_turnos
    )
    pasos: list[Paso] = []
    textos_usuario = [anterior for anterior, _ in historial] + [pregunta]
    for _ in range(max_vueltas):
        mensaje = modelo.invoke(mensajes)
        llamadas = mensaje.get("tool_calls") or []
        if not llamadas:
            en_texto = llamada_en_texto(mensaje.get("content") or "")
            if en_texto is not None:
                log.info("llamada escrita como texto: herramienta=%s", en_texto["name"])
                llamadas = [en_texto]
                mensaje = {"content": "", "tool_calls": llamadas}
        if not llamadas:
            texto = (mensaje.get("content") or "").strip()
            if not texto:
                return Respuesta(texto=SIN_RESPUESTA, motivo="sin_texto", pasos=pasos)
            if menciona_otro_cliente(texto, ctx.cliente_id):
                log.info("respuesta con otro cliente_id: se cambio por la fija")
                return Respuesta(
                    texto=RESPUESTA_OTRO_CLIENTE.format(cliente_id=ctx.cliente_id),
                    motivo="fuera_de_alcance",
                    pasos=pasos,
                )
            corregida = niega_datos_que_hay(texto, pasos)
            if corregida is not None:
                log.info("respuesta sin movimientos con datos: se uso la lectura")
                return Respuesta(texto=corregida, motivo="respuesta", pasos=pasos)
            return Respuesta(texto=texto, motivo="respuesta", pasos=pasos)
        mensajes.append(
            {
                "role": "assistant",
                "content": mensaje.get("content") or "",
                "tool_calls": llamadas,
            }
        )
        for llamada in llamadas:
            decision = revisar_llamada(
                llamada["name"], llamada.get("args") or {}, textos_usuario
            )
            if decision.rechazo is not None:
                log.info(
                    "llamada rechazada por origen: herramienta=%s", decision.nombre
                )
                resultado = {"error": decision.rechazo}
            else:
                resultado = ejecutar(decision.nombre, decision.argumentos, ctx)
                if decision.notas:
                    resultado = {**resultado, "ajustes": decision.notas}
            pasos.append(Paso(decision.nombre, decision.argumentos, resultado))
            mensajes.append(
                {
                    "role": "tool",
                    "content": json.dumps(resultado, ensure_ascii=False),
                    "tool_call_id": llamada.get("id", ""),
                }
            )
    log.info("limite de vueltas: %s herramientas ejecutadas", len(pasos))
    return Respuesta(texto=LIMITE_VUELTAS, motivo="limite_vueltas", pasos=pasos)


class ModeloOllama:
    """Adaptador a Ollama con LangChain (langchain-ollama, grupo
    "asistente"). Devuelve el mensaje del modelo como dict, que es lo que
    espera responder()."""

    def __init__(self, cfg: Configuracion):
        try:
            from langchain_ollama import ChatOllama
        except ImportError as e:
            raise ErrorConexion(
                "Falta el grupo de dependencias del asistente: "
                "uv run --group dashboard --group asistente ..."
            ) from e
        self.cfg = cfg
        self._modelo = ChatOllama(
            model=cfg.modelo,
            base_url=cfg.url,
            temperature=cfg.temperatura,
            seed=cfg.semilla,
        ).bind_tools(esquemas())

    def invoke(self, mensajes: list[dict]) -> dict:
        import httpx
        import ollama

        try:
            respuesta = self._modelo.invoke(mensajes)
        except (httpx.HTTPError, ConnectionError) as e:
            raise ErrorConexion(
                f"Ollama no responde en {self.cfg.url}. En macOS: "
                "brew services start ollama"
            ) from e
        except ollama.ResponseError as e:
            raise ErrorConexion(
                f"Ollama respondio con un error ({e}). Si falta el modelo: "
                f"ollama pull {self.cfg.modelo}"
            ) from e
        return {
            "content": respuesta.content,
            "tool_calls": [
                {"name": c["name"], "args": c["args"], "id": c.get("id") or ""}
                for c in respuesta.tool_calls
            ],
        }

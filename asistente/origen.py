"""
asistente/origen.py

Guardas deterministas entre lo que pide el modelo y lo que se ejecuta. Con
llama3.1:8b las instrucciones del prompt no alcanzaron, y pedirle que
corrigiera tampoco: ante un rechazo, en vez de reintentar le preguntaba al
usuario datos que tenian valor por defecto.

  1. Vivienda. Si la pregunta actual habla de una casa, un departamento o un
     terreno y el modelo llama a evaluar_compra, la llamada se redirige a
     simular_hipoteca con los mismos datos.
  2. Origen de los datos del usuario. Precio, plazo, tasa, enganche y renta
     solo se aceptan si ese numero aparece en lo que escribio el usuario:
       - si el argumento es opcional (tiene valor por defecto o la
         herramienta pide el dato si falta), se descarta y la herramienta
         sigue sin el;
       - si es obligatorio (el precio), la llamada no se ejecuta y el modelo
         recibe un error que le pide preguntarlo.
     Casos reales: "quiero comprarme un bmw m4, puedo?" llegaba con un precio
     de 2,500,000 que nadie dijo; la hipoteca, con tasa y enganche inventados
     aunque la descripcion pedia omitirlos.

Cada ajuste queda en Decision.notas, que el bucle agrega al resultado de la
herramienta para que el modelo y la vista "Como se obtuvo" lo vean.

Lee el texto del usuario solo para extraer numeros y unas palabras clave;
no lo registra.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from asistente.herramientas import HERRAMIENTAS, PLAZOS_ANIOS

# Argumentos que solo puede dar el usuario. Los demas (meses, categorias,
# variables) los elige el modelo a partir de la pregunta y se validan en
# las herramientas.
DEL_USUARIO = {
    "precio",
    "plazo_meses",
    "plazo_anios",
    "tasa_anual",
    "enganche_pct",
    "renta_reemplazada",
}

VIVIENDA = re.compile(
    r"\b(casa|casas|departamento|depa|depto|vivienda|terreno|hipoteca)\b"
)

_NUMERO = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(millones|millon|mdp|mil|k)?\b")
_MULTIPLICADOR = {"millones": 1e6, "millon": 1e6, "mdp": 1e6, "mil": 1e3, "k": 1e3}


@dataclass
class Decision:
    """Que se ejecuta. rechazo no None: no se ejecuta nada y el modelo recibe
    ese error."""

    nombre: str
    argumentos: dict
    rechazo: str | None = None
    notas: list[str] = field(default_factory=list)


def _sin_acentos(texto: str) -> str:
    normal = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in normal if not unicodedata.combining(c))


def numeros_del_texto(textos: list[str]) -> set[float]:
    """Cada numero que escribio el usuario y sus formas equivalentes: con
    multiplicador ("1.2 millones" = 1,200,000), como fraccion (14 = 0.14,
    para tasas y enganches) y en meses o anios (15 anios = 180 meses)."""
    valores: set[float] = set()
    for texto in textos:
        for cifra, unidad in _NUMERO.findall(_sin_acentos(texto)):
            try:
                base = float(cifra.replace(",", ""))
            except ValueError:
                continue
            numero = base * _MULTIPLICADOR.get(unidad or "", 1.0)
            valores |= {numero, base, numero / 100, numero * 12, numero / 12}
    return valores


def _aparece(valor, numeros: set[float]) -> bool:
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return True  # no es un numero: lo valida la herramienta
    if valor == 0:
        # 0 de renta o de enganche es omitirlo; 0 de precio lo rechaza la
        # herramienta.
        return True
    return any(abs(valor - n) <= max(abs(n) * 0.005, 1e-9) for n in numeros)


def _a_hipoteca(argumentos: dict) -> dict:
    """Argumentos de evaluar_compra en terminos de simular_hipoteca."""
    nuevos = {
        k: argumentos[k]
        for k in ("precio", "enganche_pct", "tasa_anual")
        if k in argumentos
    }
    try:
        anios = float(argumentos["plazo_meses"]) / 12
    except (KeyError, TypeError, ValueError):
        anios = None
    if anios is not None and anios in PLAZOS_ANIOS:
        nuevos["plazo_anios"] = int(anios)
    return nuevos


def revisar_llamada(
    nombre: str, argumentos: dict, textos_usuario: list[str]
) -> Decision:
    """Decision sobre una llamada del modelo. textos_usuario son las
    preguntas del usuario en esta conversacion, de la mas antigua a la
    actual: un dato que dio antes cuenta como dado."""
    decision = Decision(nombre, dict(argumentos or {}))
    # Vivienda: solo la pregunta actual. Con el historial, una pregunta sobre
    # un auto despues de una sobre una casa se iria a la hipoteca.
    actual = _sin_acentos(textos_usuario[-1]) if textos_usuario else ""
    if nombre == "evaluar_compra" and VIVIENDA.search(actual):
        decision.nombre = "simular_hipoteca"
        decision.argumentos = _a_hipoteca(decision.argumentos)
        decision.notas.append(
            "La pregunta es sobre vivienda: se uso simular_hipoteca en lugar de "
            "evaluar_compra."
        )
    if decision.nombre not in HERRAMIENTAS:
        return decision  # ejecutar() devuelve el error
    requeridos = set(HERRAMIENTAS[decision.nombre]["requeridos"])
    numeros = numeros_del_texto(textos_usuario)
    inventados = [
        arg
        for arg, valor in decision.argumentos.items()
        if arg in DEL_USUARIO and not _aparece(valor, numeros)
    ]
    obligatorios = [a for a in inventados if a in requeridos]
    if obligatorios:
        valores = ", ".join(f"{a}={decision.argumentos[a]}" for a in obligatorios)
        decision.rechazo = (
            f"{valores} no aparece en lo que escribio el usuario. No lo supongas: "
            "pregunta por ese dato al usuario en una frase."
        )
        return decision
    for arg in inventados:
        valor = decision.argumentos.pop(arg)
        decision.notas.append(
            f"{arg}={valor} no lo dio el usuario: se descarto y se uso el valor "
            "por defecto, o la herramienta indica que falta."
        )
    return decision


def verificar_llamada(
    nombre: str, argumentos: dict, textos_usuario: list[str]
) -> str | None:
    """Solo el rechazo de revisar_llamada (None si la llamada se ejecuta)."""
    return revisar_llamada(nombre, argumentos, textos_usuario).rechazo

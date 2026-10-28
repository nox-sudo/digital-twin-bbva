"""
asistente/origen.py

Guardas deterministas entre lo que pide el modelo y lo que se ejecuta. Con
llama3.1:8b las instrucciones del prompt no alcanzaron, y pedirle que
corrigiera tampoco: ante un rechazo, en vez de reintentar le preguntaba al
usuario datos que tenian valor por defecto.

  1. Vivienda. Si la pregunta actual habla de una casa, un departamento o un
     terreno y el modelo llama a evaluar_compra, la llamada se redirige a
     simular_hipoteca con los mismos datos.
     Flujo. Si la pregunta compara lo que entro con lo que salio ("gaste mas
     de lo que gane", "numeros rojos", "me sobro") y el modelo llama a
     gasto_por_categoria, la llamada se redirige a flujo_mensual con el
     mismo rango: gastos por categoria no tiene entradas, y en las
     evaluaciones v3 y v4 el modelo inventaba el numero de meses.
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
     Los numeros escritos con palabras cuentan ("un millón", "medio millón",
     "doscientos mil"). Una tasa de 0 solo cuenta si el usuario dijo "sin
     intereses" o 0 %: "a 6 meses" sin tasa llegaba con tasa 0.

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

FLUJO = re.compile(
    r"\b(mas de lo que (gane|ingrese|recibi|me entr\w+)|numeros rojos|en rojo|"
    r"me sobr\w+|sali\w* perdiendo|deficit|lo que (me )?entr\w+)"
)
# Argumentos de gasto_por_categoria que tambien acepta flujo_mensual.
_RANGO = ("desde", "hasta", "ultimos_meses")

_NUMERO = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(millones|millon|mdp|mil|k)?\b")
_MULTIPLICADOR = {"millones": 1e6, "millon": 1e6, "mdp": 1e6, "mil": 1e3, "k": 1e3}

_SIN_INTERESES = re.compile(
    r"\b(sin intereses|msi|tasa cero|cero por ciento)\b|\b0\s*(%|por ciento)"
)

# Numeros con palabras, sin acentos. "un", "una" y "uno" solos no cuentan:
# "un auto" no es un precio de 1.
_PALABRAS = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiun": 21,
    "veintiuno": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
    "cien": 100,
    "ciento": 100,
    "doscientos": 200,
    "doscientas": 200,
    "trescientos": 300,
    "trescientas": 300,
    "cuatrocientos": 400,
    "cuatrocientas": 400,
    "quinientos": 500,
    "quinientas": 500,
    "seiscientos": 600,
    "seiscientas": 600,
    "setecientos": 700,
    "setecientas": 700,
    "ochocientos": 800,
    "ochocientas": 800,
    "novecientos": 900,
    "novecientas": 900,
    "medio": 0.5,
}
_ESCALAS = {"mil": 1e3, "millon": 1e6, "millones": 1e6}
_PALABRA = re.compile(r"[a-z]+")


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


def _cifra(tokens: list[str]) -> float | None:
    """Valor de una secuencia de palabras numericas ("dos millones",
    "millon y medio", "doscientos cincuenta mil"), o None si no es un
    numero."""
    total, actual, escala_previa = 0.0, 0.0, None
    for token in tokens:
        if token == "y":
            continue
        if token in _ESCALAS:
            escala = _ESCALAS[token]
            if escala == 1e6:
                total = (total + (actual or 1)) * escala
            else:
                total += (actual or 1) * escala
            actual, escala_previa = 0.0, escala
        elif token == "medio" and escala_previa:
            total += 0.5 * escala_previa  # "millon y medio"
        else:
            actual += _PALABRAS[token]
    valor = total + actual
    return valor or None


def palabras_a_cifras(texto: str) -> str:
    """El texto (sin acentos) con los numeros escritos con palabras pasados
    a cifras: "una casa de un millon y medio" -> "una casa de 1500000"."""
    salida, secuencia, inicio = [], [], 0

    def cerrar(fin: int):
        nonlocal secuencia
        # La "y" final no es parte del numero ("diez mil y algo").
        while secuencia and secuencia[-1][0] == "y":
            secuencia.pop()
        palabras = [t for t, _, _ in secuencia]
        # Debe traer una palabra numerica: "mil" o "millones" solos van
        # despues de una cifra ("20 mil") y ya los lee _NUMERO.
        if any(t in _PALABRAS for t in palabras) and set(palabras) - {
            "un",
            "uno",
            "una",
            "y",
        }:
            valor = _cifra(palabras)
            if valor is not None:
                salida.append((secuencia[0][1], secuencia[-1][2], f"{valor:g}"))
        secuencia = []

    for m in _PALABRA.finditer(texto):
        token = m.group(0)
        if token in _PALABRAS or token in _ESCALAS or (token == "y" and secuencia):
            secuencia.append((token, m.start(), m.end()))
        else:
            cerrar(m.start())
    cerrar(len(texto))
    resultado = []
    for ini, fin, cifra in salida:
        resultado.append(texto[inicio:ini])
        resultado.append(f"{float(cifra):.0f}" if float(cifra) >= 1 else cifra)
        inicio = fin
    resultado.append(texto[inicio:])
    return "".join(resultado)


def numeros_del_texto(textos: list[str]) -> set[float]:
    """Cada numero que escribio el usuario y sus formas equivalentes: con
    multiplicador ("1.2 millones" = 1,200,000), con palabras ("un millón"),
    como fraccion (14 = 0.14, para tasas y enganches) y en meses o anios
    (15 anios = 180 meses)."""
    valores: set[float] = set()
    for texto in textos:
        for cifra, unidad in _NUMERO.findall(palabras_a_cifras(_sin_acentos(texto))):
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


def _es_cero(valor) -> bool:
    try:
        return float(valor) == 0
    except (TypeError, ValueError):
        return False


_ANIOS = re.compile(r"\b(\d{1,2})\s*anos?\b")


def _anios_de_la_pregunta(pregunta: str) -> int | None:
    """Plazo en anios que escribio el usuario ("a 25 años"), si es uno de los
    que acepta simular_hipoteca."""
    for cifra in _ANIOS.findall(palabras_a_cifras(_sin_acentos(pregunta))):
        if int(cifra) in PLAZOS_ANIOS:
            return int(cifra)
    return None


def _a_hipoteca(argumentos: dict, pregunta: str = "") -> dict:
    """Argumentos de evaluar_compra en terminos de simular_hipoteca. El plazo
    sale de plazo_meses si es un numero de anios valido; si no, del plazo en
    anios de la pregunta. En la evaluacion v2 el modelo escribio
    plazo_meses=25 para "a 25 años": se descartaba y la respuesta afirmaba
    25 anios sobre una simulacion a 20."""
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
    elif _anios_de_la_pregunta(pregunta) is not None:
        nuevos["plazo_anios"] = _anios_de_la_pregunta(pregunta)
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
        decision.argumentos = _a_hipoteca(decision.argumentos, textos_usuario[-1])
        decision.notas.append(
            "La pregunta es sobre vivienda: se uso simular_hipoteca en lugar de "
            "evaluar_compra."
        )
    if nombre == "gasto_por_categoria" and FLUJO.search(actual):
        decision.nombre = "flujo_mensual"
        decision.argumentos = {
            k: v for k, v in decision.argumentos.items() if k in _RANGO
        }
        decision.notas.append(
            "La pregunta compara lo que entro con lo que salio: se uso "
            "flujo_mensual en lugar de gasto_por_categoria."
        )
    if decision.nombre not in HERRAMIENTAS:
        return decision  # ejecutar() devuelve el error
    requeridos = set(HERRAMIENTAS[decision.nombre]["requeridos"])
    numeros = numeros_del_texto(textos_usuario)
    sin_intereses = any(_SIN_INTERESES.search(_sin_acentos(t)) for t in textos_usuario)
    inventados = [
        arg
        for arg, valor in decision.argumentos.items()
        if arg in DEL_USUARIO
        and (
            not _aparece(valor, numeros)
            or (arg == "tasa_anual" and _es_cero(valor) and not sin_intereses)
        )
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

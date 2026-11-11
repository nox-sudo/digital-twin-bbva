"""
asistente/documentacion.py

Busqueda en la documentacion del proyecto (README.md y docs/*.md) para la
herramienta buscar_documentacion del asistente: la parte de recuperacion del
RAG. Python recupera los fragmentos; el modelo solo los redacta y cita la
fuente, igual que con las cifras de Gold.

Como se arma el indice:
  1. Fragmentos: cada archivo se corta por encabezados (#, ##, ###). Una
     seccion larga se divide en fragmentos de hasta PALABRAS_POR_FRAGMENTO
     palabras sin partir parrafos ni filas de tabla. Cada fragmento conserva
     su ruta de encabezados ("Arquitectura > Decisiones de diseno"), que se
     indexa junto con el texto y se usa para citar.
  2. Terminos: minusculas, sin acentos, palabras vacias fuera y la raiz de
     cada palabra con el stemmer Snowball para espanol ("aceptaron" y
     "aceptados" -> "acept"; "respaldo" y "respaldar" -> "respald"). Un
     recorte solo de plurales dejaba fuera las formas verbales: "¿que riesgos
     se aceptaron?" no encontraba "Riesgos aceptados". Los identificadores con guion bajo
     (probabilidad_impago) y los nombres de archivo (business_rules.yaml) se
     conservan enteros y tambien por partes.
  3. Ranking BM25: puntua cada fragmento por los terminos de la consulta que
     contiene, con mas peso a los terminos raros en el corpus y menos a los
     fragmentos largos. Se eligio sobre embeddings porque la documentacion
     esta llena de terminos exactos (nombres de tablas, archivos, reglas), es
     determinista y se prueba en CI sin Ollama. Su limite son las
     parafrasis sin palabras en comun; experimentos/eval_rag_documentacion.py
     lo mide.

BM25 son unas lineas de Python sobre un corpus de unos cientos de
fragmentos; la unica dependencia es snowballstemmer (Python puro, sin
dependencias propias).
"""

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import snowballstemmer

RAIZ = Path(__file__).resolve().parents[1]

# README y los documentos tecnicos. Se excluyen los archivos que no son
# documentacion del proyecto.
PATRONES_CORPUS = ("README.md", "docs/*.md")

PALABRAS_POR_FRAGMENTO = 180
K1 = 1.5  # saturacion de la frecuencia de un termino
# El encabezado mas especifico de la seccion cuenta doble: resume el
# fragmento. Medido con el banco de evaluacion: lleva de 11 a 13 las
# preguntas con la seccion correcta en primer lugar (24 preguntas; 2 y 3 dan
# lo mismo dentro de una pregunta, asi que no se afina mas).
PESO_ENCABEZADO = 2
B = 0.75  # cuanto penaliza la longitud del fragmento

_ENCABEZADO = re.compile(r"^(#{1,3})\s+(.*)$")
_TERMINO = re.compile(r"[a-z0-9]+(?:[_./-][a-z0-9]+)*")

# Palabras vacias del espanol que aparecen en casi todas las preguntas.
VACIAS = frozenset("""
    a al algo como con cual cuales cuando de del donde el ella ellos en entre
    es esa ese eso esta estan este esto ha hay la las le les lo los mas me mi
    mis muy no o para pero por porque que quien se si sin sobre son su sus
    tiene tienen un una uno unos y ya yo tu te nos fue ser hace hacen puede
    pueden cada cuanto cuanta cuantos cuantas qu
    """.split())


@dataclass(frozen=True)
class Fragmento:
    archivo: str  # ruta relativa a la raiz del repo
    seccion: str  # ruta de encabezados, separada por " > "
    texto: str

    @property
    def fuente(self) -> str:
        return f"{self.archivo} § {self.seccion}" if self.seccion else self.archivo


def sin_acentos(texto: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


_STEMMER = snowballstemmer.stemmer("spanish")


@lru_cache(maxsize=20_000)
def _raiz(palabra: str) -> str:
    return _STEMMER.stemWord(palabra)


def terminos(texto: str) -> list[str]:
    """Terminos de busqueda de un texto. Un identificador compuesto cuenta
    entero y por partes: "probabilidad_impago" da probabilidad_impago,
    probabilidad e impago."""
    salida = []
    for token in _TERMINO.findall(sin_acentos(texto.lower())):
        partes = [t for t in re.split(r"[_./-]", token) if t]
        if len(partes) > 1:
            # El identificador es un nombre exacto: entero y sin raiz.
            salida.append(token)
        for t in partes:
            if t not in VACIAS and not t.isdigit():
                salida.append(_raiz(t))
    return salida


def _bloques(lineas: list[str]) -> list[str]:
    """Parrafos y filas de tabla como unidades que no se parten. Un bloque
    de codigo cuenta como una sola unidad."""
    bloques, actual, en_codigo = [], [], False
    for linea in lineas:
        if linea.strip().startswith("```"):
            en_codigo = not en_codigo
            actual.append(linea)
            if not en_codigo:
                bloques.append("\n".join(actual))
                actual = []
            continue
        if en_codigo:
            actual.append(linea)
        elif not linea.strip():
            if actual:
                bloques.append("\n".join(actual))
                actual = []
        elif linea.lstrip().startswith("|"):
            if actual and not actual[-1].lstrip().startswith("|"):
                bloques.append("\n".join(actual))
                actual = []
            # Cada fila de tabla es su propio bloque; la de separadores no aporta.
            if not re.fullmatch(r"\s*\|[\s:|-]+\|\s*", linea):
                bloques.append(linea)
        else:
            actual.append(linea)
    if actual:
        bloques.append("\n".join(actual))
    return bloques


def fragmentar(texto: str, archivo: str) -> list[Fragmento]:
    fragmentos: list[Fragmento] = []
    ruta: list[str] = []
    lineas: list[str] = []

    def cerrar():
        seccion = " > ".join(ruta)
        actual: list[str] = []
        palabras = 0
        for bloque in _bloques(lineas):
            n = len(bloque.split())
            if actual and palabras + n > PALABRAS_POR_FRAGMENTO:
                fragmentos.append(Fragmento(archivo, seccion, "\n\n".join(actual)))
                actual, palabras = [], 0
            actual.append(bloque)
            palabras += n
        if actual:
            fragmentos.append(Fragmento(archivo, seccion, "\n\n".join(actual)))

    en_codigo = False
    for linea in texto.splitlines():
        if linea.strip().startswith("```"):
            en_codigo = not en_codigo
        m = None if en_codigo else _ENCABEZADO.match(linea)
        if m:
            cerrar()
            nivel = len(m.group(1))
            ruta = ruta[: nivel - 1] + [m.group(2).strip()]
            lineas = []
        else:
            lineas.append(linea)
    cerrar()
    return fragmentos


@dataclass(frozen=True)
class Resultado:
    fragmento: Fragmento
    puntaje: float


class Indice:
    def __init__(self, fragmentos: list[Fragmento]):
        self.fragmentos = fragmentos
        # El encabezado se indexa con el texto: "Secretos" debe encontrar el
        # fragmento de esa seccion aunque el texto no repita la palabra.
        self._terminos = [
            Counter(terminos(f.texto))
            + Counter(terminos(f.seccion.split(" > ")[-1]) * PESO_ENCABEZADO)
            for f in fragmentos
        ]
        self._largos = [sum(c.values()) for c in self._terminos]
        self._largo_medio = (sum(self._largos) / len(self._largos)) if fragmentos else 0
        documentos_con = Counter(t for c in self._terminos for t in c)
        n = len(fragmentos)
        self._idf = {
            t: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for t, df in documentos_con.items()
        }

    @classmethod
    def desde_archivos(cls, raiz: Path = RAIZ, patrones=PATRONES_CORPUS) -> "Indice":
        fragmentos = []
        for patron in patrones:
            for ruta in sorted(raiz.glob(patron)):
                relativo = ruta.relative_to(raiz).as_posix()
                fragmentos += fragmentar(ruta.read_text(encoding="utf-8"), relativo)
        return cls(fragmentos)

    def buscar(self, consulta: str, k: int = 3) -> list[Resultado]:
        """Los k fragmentos con mayor puntaje BM25. Un fragmento sin ningun
        termino de la consulta no se devuelve: puntaje 0 no es un resultado."""
        consulta_terminos = set(terminos(consulta))
        puntajes = []
        for i, conteo in enumerate(self._terminos):
            puntaje = 0.0
            for t in consulta_terminos:
                f = conteo.get(t)
                if not f:
                    continue
                norma = K1 * (1 - B + B * self._largos[i] / self._largo_medio)
                puntaje += self._idf[t] * f * (K1 + 1) / (f + norma)
            if puntaje > 0:
                puntajes.append((puntaje, i))
        puntajes.sort(key=lambda p: (-p[0], p[1]))
        return [Resultado(self.fragmentos[i], p) for p, i in puntajes[:k]]


@lru_cache(maxsize=1)
def indice_del_repo() -> Indice:
    """Se arma una vez por proceso: el dashboard no relee 4,000 lineas de
    markdown en cada pregunta."""
    return Indice.desde_archivos()

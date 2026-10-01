"""
src/common/identificadores.py

Construccion y validacion de identificadores oficiales mexicanos de
persona fisica: CURP (RENAPO) y RFC (SAT).

Un solo modulo para las dos direcciones: el generador sintetico los
CONSTRUYE a partir del nombre, fecha de nacimiento, sexo y estado de
cada cliente, y Silver los VALIDA con las mismas reglas. Si las reglas
vivieran en dos lugares, un cambio en uno haria que Silver rechazara
datos correctos (o aceptara incorrectos) sin que nadie lo notara.

Que se valida:
- Formato (expresion regular, incluida la lista de claves de estado).
- Digito verificador: el ultimo caracter se calcula a partir de los
  anteriores. Detecta errores de captura (un caracter cambiado) que el
  formato solo no detecta.
- Coherencia con el cliente: los 6 digitos de fecha deben coincidir con
  su fecha_nacimiento. Una CURP con formato y digito correctos pero de
  otra persona no pasa.

Los algoritmos siguen los instructivos publicos de RENAPO (CURP) y del
SAT (homoclave y digito verificador del RFC).
"""

import re
import unicodedata
from datetime import date

# Claves de entidad federativa en la CURP. NE = nacido en el extranjero.
ESTADOS_CURP = (
    "AS BC BS CC CL CM CS CH DF DG GT GR HG JC MC MN MS NT NL OC PL QT QR SP "
    "SL SR TC TS TL VZ YN ZS NE"
).split()

# Particulas que no cuentan para formar la clave (de la Garza -> GARZA).
_PARTICULAS = {
    "DA", "DAS", "DE", "DEL", "DER", "DI", "DIE", "DD", "EL", "LA", "LOS",
    "LAS", "LE", "LES", "MAC", "MC", "VAN", "VON", "Y",
}  # fmt: skip

# Nombres que se omiten si hay un segundo nombre (Maria Elena -> ELENA).
_NOMBRES_COMUNES = {"MARIA", "MA", "MA.", "M", "JOSE", "J", "J."}

# Combinaciones de 4 letras que RENAPO y el SAT no permiten (lista
# oficial del instructivo); se corrige reemplazando una letra por X.
_PALABRAS_INCONVENIENTES = set(
    """BACA BAKA BUEI BUEY CACA CACO CAGA CAGO CAKA CAKO COGE COGI COJA COJE
    COJI COJO COLA CULO FALO FETO GETA GUEI GUEY JETA JOTO KACA KACO KAGA KAGO
    KAKA KAKO KOGE KOGI KOJA KOJE KOJI KOJO KOLA KULO LILO LOCA LOCO LOKA LOKO
    MAME MAMO MEAR MEAS MEON MIAR MION MOCO MOKO MULA MULO NACA NACO PEDA PEDO
    PENE PIPI PITO POPO PUTA PUTO QULO RATA ROBA ROBE ROBO RUIN SENO TETA VACA
    VAGA VAGO VAKA VUEI VUEY WUEI WUEY""".split()
)

_VOCALES = "AEIOU"

REGEX_CURP = re.compile(
    r"^[A-Z][AEIOUX][A-Z]{2}\d{6}[HM](" + "|".join(ESTADOS_CURP) + r")"
    r"[B-DF-HJ-NP-TV-Z]{3}[A-Z\d]\d$"
)
REGEX_RFC_FISICA = re.compile(r"^[A-ZÑ&]{4}\d{6}[A-Z\d]{2}[\dA]$")

# Tablas de los algoritmos de digito verificador y homoclave. No son
# secretos: el comentario "pragma" evita el falso positivo del escaneo de
# entropia de CI, que las confunde con llaves aleatorias.
DICC_CURP = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"  # pragma: allowlist secret
DICC_RFC = "0123456789ABCDEFGHIJKLMN&OPQRSTUVWXYZ Ñ"  # pragma: allowlist secret
_TABLA_HOMOCLAVE = "123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"  # pragma: allowlist secret


# --------------------------------------------------------------------------
# Normalizacion
# --------------------------------------------------------------------------


def _sin_acentos(texto: str) -> str:
    """Quita acentos y dieresis pero conserva la Ñ."""
    texto = texto.upper().replace("Ñ", "\0")
    texto = "".join(
        c
        for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    return texto.replace("\0", "Ñ")


def _palabras(texto: str) -> list[str]:
    limpio = re.sub(r"[^A-ZÑ ]", "", _sin_acentos(texto or ""))
    return [p for p in limpio.split() if p]


def _sin_particulas(texto: str) -> str:
    palabras = [p for p in _palabras(texto) if p not in _PARTICULAS]
    return " ".join(palabras)


def _nombre_para_clave(nombre: str) -> str:
    palabras = _sin_particulas(nombre).split()
    if len(palabras) > 1 and palabras[0] in _NOMBRES_COMUNES:
        palabras = palabras[1:]
    return " ".join(palabras)


def _primera_vocal_interna(palabra: str) -> str:
    for c in palabra[1:]:
        if c in _VOCALES:
            return c
    return "X"


def _primera_consonante_interna(palabra: str) -> str:
    for c in palabra[1:]:
        if c.isalpha() and c not in _VOCALES:
            return "X" if c == "Ñ" else c
    return "X"


def _letras_iniciales(paterno: str, materno: str, nombre: str) -> str:
    """Las 4 letras comunes a CURP y RFC, antes de corregir palabras
    inconvenientes."""
    pat = _sin_particulas(paterno).split()[0]
    mat_palabras = _sin_particulas(materno).split()
    nom = _nombre_para_clave(nombre).split()[0]
    if mat_palabras:
        letras = pat[0] + _primera_vocal_interna(pat) + mat_palabras[0][0] + nom[0]
    else:
        # Sin apellido materno: dos letras del paterno y dos del nombre.
        letras = pat[0] + _primera_vocal_interna(pat) + nom[:2].ljust(2, "X")
    return letras.replace("Ñ", "X")


# --------------------------------------------------------------------------
# CURP
# --------------------------------------------------------------------------


def digito_verificador_curp(curp17: str) -> str:
    suma = sum(DICC_CURP.index(c) * (18 - i) for i, c in enumerate(curp17))
    return str((10 - suma % 10) % 10)


def construir_curp(
    nombre: str,
    paterno: str,
    materno: str,
    fecha_nacimiento: date,
    sexo: str,
    estado: str,
    homoclave: str,
) -> str:
    """CURP completa. homoclave es el caracter 17 (lo asigna RENAPO para
    evitar duplicados): digito si nacio antes de 2000, letra desde 2000."""
    letras = _letras_iniciales(paterno, materno, nombre)
    if letras in _PALABRAS_INCONVENIENTES:
        letras = letras[0] + "X" + letras[2:]

    pat = _sin_particulas(paterno).split()[0]
    mat = (_sin_particulas(materno).split() or [""])[0]
    nom = _nombre_para_clave(nombre).split()[0]
    consonantes = (
        _primera_consonante_interna(pat)
        + (_primera_consonante_interna(mat) if mat else "X")
        + _primera_consonante_interna(nom)
    )
    curp17 = (
        letras
        + fecha_nacimiento.strftime("%y%m%d")
        + sexo
        + estado
        + consonantes
        + homoclave
    )
    return curp17 + digito_verificador_curp(curp17)


def validar_curp(curp: str | None, fecha_nacimiento: date | None = None) -> bool:
    """Formato, digito verificador, fecha real, homoclave coherente con
    el siglo, y (si se pasa) coincidencia con fecha_nacimiento."""
    if not curp or not REGEX_CURP.match(curp):
        return False
    if digito_verificador_curp(curp[:17]) != curp[17]:
        return False
    fecha_curp = _fecha_de_clave(curp[4:10], siglo_2000=curp[16].isalpha())
    if fecha_curp is None:
        return False
    if fecha_nacimiento is not None and fecha_curp != fecha_nacimiento:
        return False
    return True


# --------------------------------------------------------------------------
# RFC
# --------------------------------------------------------------------------


def homoclave_rfc(nombre: str, paterno: str, materno: str) -> str:
    """Homoclave del SAT: 2 caracteres calculados del nombre completo."""
    nombre_completo = " ".join(
        p
        for p in (_sin_acentos(paterno), _sin_acentos(materno), _sin_acentos(nombre))
        if p
    )

    def codigo(c: str) -> str:
        if c == " ":
            return "00"
        if c.isdigit():
            return f"0{c}"
        if c == "&":
            return "10"
        if c == "Ñ":
            return "40"
        if "A" <= c <= "I":
            return str(ord(c) - ord("A") + 11)
        if "J" <= c <= "R":
            return str(ord(c) - ord("J") + 21)
        if "S" <= c <= "Z":
            return str(ord(c) - ord("S") + 32)
        return "00"

    cadena = "0" + "".join(codigo(c) for c in nombre_completo)
    # Cada par de digitos consecutivos, multiplicado por su segundo digito.
    suma = sum(
        int(cadena[i] + cadena[i + 1]) * int(cadena[i + 1])
        for i in range(len(cadena) - 1)
    )
    ultimos3 = suma % 1000
    return _TABLA_HOMOCLAVE[ultimos3 // 34] + _TABLA_HOMOCLAVE[ultimos3 % 34]


def digito_verificador_rfc(rfc12: str) -> str:
    suma = sum(DICC_RFC.index(c) * (13 - i) for i, c in enumerate(rfc12))
    residuo = suma % 11
    if residuo == 0:
        return "0"
    digito = 11 - residuo
    return "A" if digito == 10 else str(digito)


def construir_rfc(
    nombre: str, paterno: str, materno: str, fecha_nacimiento: date
) -> str:
    letras = _letras_iniciales(paterno, materno, nombre)
    if letras in _PALABRAS_INCONVENIENTES:
        letras = letras[:3] + "X"
    rfc12 = (
        letras
        + fecha_nacimiento.strftime("%y%m%d")
        + homoclave_rfc(nombre, paterno, materno)
    )
    return rfc12 + digito_verificador_rfc(rfc12)


def validar_rfc(rfc: str | None, fecha_nacimiento: date | None = None) -> bool:
    """Formato de persona fisica, digito verificador, fecha real y (si se
    pasa) coincidencia con fecha_nacimiento."""
    if not rfc or not REGEX_RFC_FISICA.match(rfc):
        return False
    if digito_verificador_rfc(rfc[:12]) != rfc[12]:
        return False
    if fecha_nacimiento is None:
        return _fecha_de_clave(rfc[4:10], siglo_2000=None) is not None
    return rfc[4:10] == fecha_nacimiento.strftime("%y%m%d")


# --------------------------------------------------------------------------


def _fecha_de_clave(aammdd: str, siglo_2000: bool | None) -> date | None:
    """Convierte AAMMDD a fecha. La CURP indica el siglo con su caracter
    17 (letra = 2000+); el RFC no lo indica, y entonces solo se verifica
    que la fecha exista en alguno de los dos siglos."""
    anio, mes, dia = int(aammdd[:2]), int(aammdd[2:4]), int(aammdd[4:6])
    siglos = [2000] if siglo_2000 else [1900] if siglo_2000 is False else [1900, 2000]
    for siglo in siglos:
        try:
            return date(siglo + anio, mes, dia)
        except ValueError:
            continue
    return None

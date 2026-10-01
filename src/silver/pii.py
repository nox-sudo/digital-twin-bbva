"""
src/silver/pii.py

Proteccion de datos personales al escribir Silver, segun la politica
declarada en config/politica_pii.yaml. Como el motor de validacion, es
generico: agregar un campo sensible es una linea en la politica, no
codigo nuevo.

Tres tratamientos (detalle y ejemplos en docs/seguridad.md):

- hash_y_mascara: la columna original se reemplaza por <col>_hash (para
  unir, deduplicar y contar sin ver el valor) y <col>_mascara (para que
  una persona reconozca el registro sin verlo completo).
- solo_hash: se reemplaza por <col>_hash; no queda version legible.
- descartar: la columna no llega a Silver (por ejemplo, calle y numero:
  se conservan codigo postal, municipio y estado, que bastan para
  analisis por region).

El hash es HMAC-SHA256 con la sal secreta PII_HASH_SALT, calculado con
expresiones nativas de Spark (sin UDF de Python). HMAC es la
construccion estandar para "hash con secreto":

    HMAC(k, m) = SHA256( (k XOR opad) || SHA256( (k XOR ipad) || m ) )

La llave es fija durante toda la corrida, asi que (k XOR ipad) y
(k XOR opad) se precalculan en Python como bytes y entran a Spark como
literales; Spark solo hace dos sha2 y concatenaciones por fila.
tests/test_pii.py verifica que el resultado coincide byte por byte con
hmac.new() de la libreria estandar de Python.
"""

import hashlib

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

_TAMANO_BLOQUE_SHA256 = 64


def _llaves_hmac(sal: str) -> tuple[bytes, bytes]:
    llave = sal.encode("utf-8")
    if len(llave) > _TAMANO_BLOQUE_SHA256:
        llave = hashlib.sha256(llave).digest()
    llave = llave.ljust(_TAMANO_BLOQUE_SHA256, b"\x00")
    llave_interna = bytes(b ^ 0x36 for b in llave)
    llave_externa = bytes(b ^ 0x5C for b in llave)
    return llave_interna, llave_externa


def hmac_sha256(columna: Column, sal: str) -> Column:
    """HMAC-SHA256 en hexadecimal. NULL si la columna es NULL."""
    llave_interna, llave_externa = _llaves_hmac(sal)
    mensaje = F.encode(columna.cast("string"), "UTF-8")
    hash_interno = F.unhex(F.sha2(F.concat(F.lit(llave_interna), mensaje), 256))
    return F.sha2(F.concat(F.lit(llave_externa), hash_interno), 256)


# --- Mascaras: que parte del valor queda visible ---------------------------
# En SQL de Spark porque se leen mejor que su equivalente en la API de
# columnas. `{c}` es el nombre de la columna (entre backticks por si
# tuviera caracteres especiales).

MASCARAS = {
    # GOMA850312HSLRRN09 -> GOMA************09
    "identificador": (
        "concat(substr(`{c}`, 1, 4), repeat('*', greatest(length(`{c}`) - 6, 0)), "
        "substr(`{c}`, -2, 2))"
    ),
    # 6670542351 -> ******2351
    "telefono": "concat(repeat('*', greatest(length(`{c}`) - 4, 0)), substr(`{c}`, -4, 4))",
    # cesar.alvarado55@example.com -> c***@example.com
    "email": "concat(substr(`{c}`, 1, 1), '***@', substring_index(`{c}`, '@', -1))",
}


def enmascarar(columna: str, tipo_mascara: str) -> Column:
    if tipo_mascara not in MASCARAS:
        raise ValueError(
            f"Tipo de mascara '{tipo_mascara}' no existe para '{columna}'. "
            f"Disponibles: {sorted(MASCARAS)}"
        )
    return F.expr(MASCARAS[tipo_mascara].format(c=columna))


def proteger_pii(df: DataFrame, politica: dict | None, sal: str) -> DataFrame:
    """Aplica la politica de una entidad. Sin politica, devuelve el
    DataFrame sin cambios. Columnas de la politica que no existan en el
    DataFrame se ignoran (por ejemplo, en una cuarentena vacia)."""
    if not politica:
        return df

    for columna, tipo_mascara in politica.get("hash_y_mascara", {}).items():
        if columna not in df.columns:
            continue
        df = (
            df.withColumn(f"{columna}_hash", hmac_sha256(F.col(columna), sal))
            .withColumn(f"{columna}_mascara", enmascarar(columna, tipo_mascara))
            .drop(columna)
        )

    for columna in politica.get("solo_hash", []):
        if columna in df.columns:
            df = df.withColumn(
                f"{columna}_hash", hmac_sha256(F.col(columna), sal)
            ).drop(columna)

    columnas_a_descartar = [c for c in politica.get("descartar", []) if c in df.columns]
    return df.drop(*columnas_a_descartar)


def columnas_en_claro(df: DataFrame, politica: dict | None) -> list[str]:
    """Columnas de la politica que siguen presentes sin proteger. Se usa
    como verificacion final antes de escribir: debe ser una lista vacia."""
    if not politica:
        return []
    sensibles = (
        set(politica.get("hash_y_mascara", {}))
        | set(politica.get("solo_hash", []))
        | set(politica.get("descartar", []))
    )
    return sorted(sensibles & set(df.columns))

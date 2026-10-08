"""
src/silver/identificadores_spark.py

Validacion de CURP y RFC como expresiones nativas de Spark, para el
motor de reglas de Silver.

Por que no reutilizar directamente src/common/identificadores.py con
una UDF de Python: una UDF saca cada fila de la JVM, la serializa hacia
un proceso de Python, ejecuta la funcion y la regresa. Con expresiones
nativas, Spark evalua todo dentro de la JVM, columna por columna, y
puede optimizarlo junto con el resto del plan. Para miles de clientes la
diferencia es pequena; para un volumen bancario real, es la diferencia
entre segundos y minutos.

El costo es tener la logica del digito verificador en dos lugares. Se
controla con una prueba (tests/test_pii.py) que compara esta version
contra la de Python sobre cientos de identificadores validos e
invalidos: si alguien cambia una y no la otra, la prueba falla.
"""

from pyspark.sql import Column
from pyspark.sql import functions as F

from src.common.identificadores import (
    REGEX_CURP,
    REGEX_RFC_FISICA,
    DICC_CURP,
    DICC_RFC,
)


def _valor_en_diccionario(diccionario: str, caracter: Column) -> Column:
    """Posicion (desde 0) del caracter en el diccionario del algoritmo."""
    return F.instr(F.lit(diccionario), caracter) - 1


def _caracter(columna: Column, posicion: int) -> Column:
    """Caracter en la posicion dada, contando desde 0."""
    return F.substring(columna, posicion + 1, 1)


def curp_valida(curp: str, fecha_nacimiento: str) -> Column:
    """True si la CURP tiene formato valido, digito verificador correcto,
    y su fecha y siglo coinciden con fecha_nacimiento. NULL si la CURP
    es NULL (la presencia la valida not_null, no esta regla)."""
    c = F.col(curp)
    suma = sum(
        (
            _valor_en_diccionario(DICC_CURP, _caracter(c, i)) * (18 - i)
            for i in range(17)
        ),
        F.lit(0),
    )
    digito_esperado = ((F.lit(10) - suma % 10) % 10).cast("string")
    fecha = F.col(fecha_nacimiento)
    return (
        c.rlike(REGEX_CURP.pattern)
        & (_caracter(c, 17) == digito_esperado)
        & (F.substring(c, 5, 6) == F.date_format(fecha, "yyMMdd"))
        # Caracter 17: letra si nacio desde 2000, digito si antes.
        & (_caracter(c, 16).rlike("^[A-Z]$") == (F.year(fecha) >= 2000))
    )


def rfc_valido(rfc: str, fecha_nacimiento: str) -> Column:
    """True si el RFC de persona fisica tiene formato valido, digito
    verificador correcto y su fecha coincide con fecha_nacimiento."""
    r = F.col(rfc)
    suma = sum(
        (
            _valor_en_diccionario(DICC_RFC, _caracter(r, i)) * (13 - i)
            for i in range(12)
        ),
        F.lit(0),
    )
    residuo = suma % 11
    digito_esperado = (
        F.when(residuo == 0, F.lit("0"))
        .when(residuo == 1, F.lit("A"))
        .otherwise((F.lit(11) - residuo).cast("string"))
    )
    return (
        r.rlike(REGEX_RFC_FISICA.pattern)
        & (_caracter(r, 12) == digito_esperado)
        & (F.substring(r, 5, 6) == F.date_format(F.col(fecha_nacimiento), "yyMMdd"))
    )


VALIDADORES = {
    "curp": curp_valida,
    "rfc": rfc_valido,
}

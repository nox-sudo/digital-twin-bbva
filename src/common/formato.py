"""
src/common/formato.py

Formato de montos en pesos mexicanos. Vive aqui (sin Streamlit) para que lo
usen igual el dashboard, el asistente y la escalera financiera.

  formato_mxn(20407)                  -> "$20,407 MXN"  (vistas de administracion)
  formato_mxn(20407, estilo="cliente") -> "$20,407"      (texto para el cliente)
"""

import math

ESTILOS = ("admin", "cliente")


def formato_mxn(valor, estilo: str = "admin", decimales: int = 0) -> str:
    """Monto en pesos con separador de miles. "sin dato" si falta."""
    if estilo not in ESTILOS:
        raise ValueError(f"estilo debe ser uno de {ESTILOS}: {estilo!r}")
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return "sin dato"
    valor = float(valor)
    signo = "-" if round(valor, decimales) < 0 else ""
    texto = f"{signo}${abs(valor):,.{decimales}f}"
    return f"{texto} MXN" if estilo == "admin" else texto

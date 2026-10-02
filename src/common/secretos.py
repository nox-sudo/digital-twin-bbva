"""
src/common/secretos.py

Lectura de secretos (por ahora, PII_HASH_SALT). Orden de busqueda:

1. Variable de entorno. Es el camino en Docker: compose y el DAG la
   inyectan desde .env (el DAG via private_environment).
2. Archivo .env en la raiz del proyecto. Es el camino al correr los
   scripts directo con uv, sin Docker (desarrollo local).

Si no aparece en ninguno, se falla con un mensaje accionable en vez de
usar un valor por default: un default silencioso haria que los hashes
de PII se calcularan con una sal conocida por cualquiera que lea el
codigo, que es lo mismo que no tener sal.
"""

import os
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]


def _leer_env(ruta: Path) -> dict:
    valores = {}
    if not ruta.exists():
        return valores
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        valores[clave.strip()] = valor.strip()
    return valores


def obtener_secreto(nombre: str, ruta_env: Path | None = None) -> str:
    valor = os.environ.get(nombre) or _leer_env(ruta_env or RAIZ_PROYECTO / ".env").get(
        nombre
    )
    if not valor:
        raise RuntimeError(
            f"Falta el secreto {nombre}. Corre 'bash setup.sh' para generar .env, "
            f"o define la variable de entorno {nombre}."
        )
    return valor

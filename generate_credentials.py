"""
generate_credentials.py

Crea los usuarios del dashboard a partir de los clientes de Gold: un
admin y un usuario por cliente (el usuario es su cliente_id), con
contrasenas aleatorias nuevas en cada corrida (src/common/usuarios.py).

  - data/auth/usuarios.duckdb: usuario, rol y hash argon2id. Nunca la
    contrasena.
  - data/credenciales_prueba.md: las contrasenas en claro, para probar el
    dashboard. Esta en data/, que git ignora.

Corre despues de Gold y no la modifica. Volver a correrlo invalida las
contrasenas anteriores. No necesita Spark.

Uso:
    uv run --group dashboard python generate_credentials.py
"""

import argparse
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.usuarios import crear_usuarios  # noqa: E402


def clientes_gold(ruta_gold: str) -> list[str]:
    con = duckdb.connect(ruta_gold, read_only=True)
    try:
        filas = con.execute(
            "SELECT DISTINCT cliente_id FROM gold_features_cliente ORDER BY cliente_id"
        ).fetchall()
    finally:
        con.close()
    return [f[0] for f in filas]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Usuarios de prueba del dashboard")
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument("--usuarios", default="data/auth/usuarios.duckdb")
    parser.add_argument("--credenciales", default="data/credenciales_prueba.md")
    args = parser.parse_args(argv)

    if not Path(args.gold).exists():
        print(f"No existe {args.gold}: corre el pipeline antes.", file=sys.stderr)
        return 1
    clientes = clientes_gold(args.gold)
    inicio = time.monotonic()
    total = crear_usuarios(clientes, args.usuarios, args.credenciales)
    print(
        f"{total} usuarios (1 admin, {len(clientes)} clientes) en {args.usuarios}, "
        f"en {time.monotonic() - inicio:.0f} s. Contraseñas en {args.credenciales}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

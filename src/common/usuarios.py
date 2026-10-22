"""
src/common/usuarios.py

Usuarios del dashboard y su verificacion. Dos roles:

  - admin: ve el dashboard completo (portafolio, cualquier cliente,
    observabilidad).
  - cliente: el usuario es su cliente_id y solo ve sus propios datos.

Las contrasenas se generan al azar en cada corrida (modulo secrets, no la
semilla de los datos: la semilla esta en el repositorio publico y con ella
cualquiera podria reproducirlas). La base de usuarios guarda solo el hash
argon2id; las contrasenas en claro van unicamente al archivo de
credenciales de prueba, dentro de data/ (ignorado por git).
"""

import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

ROLES = ("admin", "cliente")
USUARIO_ADMIN = "admin"
TABLA = "usuarios"

# Parametros por default de argon2-cffi (perfil de RFC 9106 con poca
# memoria: argon2id, 3 pasadas, 64 MiB). Las pruebas pasan uno mas ligero.
HASHER = PasswordHasher()


@dataclass(frozen=True)
class Usuario:
    usuario: str
    rol: str


def generar_contrasena() -> str:
    """16 caracteres de un alfabeto URL-safe (96 bits de entropia)."""
    return secrets.token_urlsafe(12)


def crear_usuarios(
    clientes: list[str],
    ruta_db: str | Path,
    ruta_credenciales: str | Path,
    hasher: PasswordHasher | None = None,
) -> int:
    """Crea la base de usuarios (un admin y un usuario por cliente) y el
    archivo de credenciales de prueba. Reemplaza los de la corrida anterior:
    las contrasenas viejas dejan de servir. Devuelve cuantos usuarios creo."""
    hasher = hasher or HASHER
    ruta_db, ruta_credenciales = Path(ruta_db), Path(ruta_credenciales)
    cuentas = [(USUARIO_ADMIN, "admin")] + [(c, "cliente") for c in sorted(clientes)]
    if len({u for u, _ in cuentas}) != len(cuentas):
        raise ValueError("Hay clientes repetidos o un cliente llamado 'admin'")

    creado = datetime.now().replace(microsecond=0)
    contrasenas = {u: generar_contrasena() for u, _ in cuentas}
    filas = [(u, rol, hasher.hash(contrasenas[u]), creado) for u, rol in cuentas]

    # Se escribe aparte y se reemplaza al final, para que el dashboard nunca
    # lea una base a medias.
    ruta_db.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta_db.with_suffix(".tmp")
    temporal.unlink(missing_ok=True)
    con = duckdb.connect(str(temporal))
    con.execute(
        f"CREATE TABLE {TABLA} (usuario VARCHAR PRIMARY KEY, rol VARCHAR NOT NULL, "
        "hash VARCHAR NOT NULL, creado TIMESTAMP NOT NULL)"
    )
    con.executemany(f"INSERT INTO {TABLA} VALUES (?, ?, ?, ?)", filas)
    con.close()
    os.replace(temporal, ruta_db)

    escribir_credenciales(ruta_credenciales, cuentas, contrasenas, creado)
    return len(cuentas)


def escribir_credenciales(
    ruta: Path, cuentas: list[tuple[str, str]], contrasenas: dict, creado
) -> None:
    lineas = [
        "# Credenciales de prueba del dashboard",
        "",
        f"Generadas el {creado:%Y-%m-%d %H:%M} por generate_credentials.py. "
        "Cada corrida genera contraseñas nuevas e invalida estas.",
        "",
        "Archivo local: está en data/, que git ignora. No lo subas ni lo "
        "compartas fuera del equipo.",
        "",
        "| Usuario | Rol | Contraseña |",
        "|---|---|---|",
        *[f"| {u} | {rol} | `{contrasenas[u]}` |" for u, rol in cuentas],
        "",
    ]
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # Solo lectura y escritura para el dueno del archivo.
    descriptor = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))


def verificar(
    ruta_db: str | Path,
    usuario: str,
    contrasena: str,
    hasher: PasswordHasher | None = None,
) -> Usuario | None:
    """El usuario si la contrasena es correcta; None en cualquier otro caso
    (usuario inexistente incluido), sin decir cual fallo."""
    hasher = hasher or HASHER
    con = duckdb.connect(str(ruta_db), read_only=True)
    try:
        fila = con.execute(
            f"SELECT rol, hash FROM {TABLA} WHERE usuario = ?", [usuario.strip()]
        ).fetchone()
    finally:
        con.close()
    if fila is None:
        # Se calcula un hash igual para que un usuario inexistente tarde lo
        # mismo que una contrasena equivocada.
        hasher.hash(contrasena)
        return None
    rol, hash_guardado = fila
    try:
        hasher.verify(hash_guardado, contrasena)
    except (VerificationError, InvalidHashError):
        return None
    if rol not in ROLES:
        return None
    return Usuario(usuario=usuario.strip(), rol=rol)

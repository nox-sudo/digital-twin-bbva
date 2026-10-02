"""
src/bronze/control.py

Control de ingesta incremental de Bronze: que archivos ya se ingirieron
y cuales faltan.

Bronze deja de sobrescribirse en cada corrida: acumula. Cada archivo de
origen se ingiere una sola vez, identificado por su ruta y su SHA-256,
y queda anotado en un registro de control (data/bronze/_control_ingesta.jsonl,
una linea JSON por archivo). Reingerir la misma entrega no agrega nada;
una entrega nueva solo aporta lo que trae de distinto.

Garantia de entrega: "al menos una vez". El registro se escribe despues
de que Spark confirma la escritura en Bronze. Si el proceso muere entre
ambas cosas, la siguiente corrida vuelve a ingerir ese archivo y Bronze
queda con filas repetidas; Silver ya deduplica por llave primaria
(conservando la mas reciente), asi que el resultado final es el mismo
que si se hubiera ingerido una sola vez. En produccion este registro
seria una tabla transaccional; aqui un JSONL basta y se puede leer a
simple vista.

Todo este modulo es Python puro, sin Spark, para poder probar la logica
de planeacion sin levantar un cluster.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

NOMBRE_CONTROL = "_control_ingesta.jsonl"

ENTIDADES_POR_ARCHIVO = {
    "clientes.csv": "clientes",
    "catalogo_productos.json": "catalogo_productos",
    "cuentas.json": "cuentas",
    "cetes_inversiones.csv": "cetes_inversiones",
}


def entidad_de(ruta: str) -> str | None:
    """Entidad de Bronze a la que pertenece un archivo de origen."""
    if ruta.startswith("transacciones/") and ruta.endswith(".csv"):
        return "transacciones"
    return ENTIDADES_POR_ARCHIVO.get(ruta)


@dataclass(frozen=True)
class Entrega:
    entrega_id: str
    carpeta: Path
    archivos: dict  # ruta relativa -> sha256


@dataclass(frozen=True)
class ArchivoPendiente:
    entrega_id: str
    carpeta: Path
    ruta: str
    sha256: str
    entidad: str


def leer_control(carpeta_bronze: Path) -> set[tuple[str, str]]:
    """(ruta, sha256) de todo lo ya ingerido."""
    ruta = carpeta_bronze / NOMBRE_CONTROL
    if not ruta.exists():
        return set()
    ingeridos = set()
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea.strip():
            registro = json.loads(linea)
            ingeridos.add((registro["archivo"], registro["sha256"]))
    return ingeridos


def anotar_ingeridos(
    carpeta_bronze: Path, pendientes: list[ArchivoPendiente], filas_por_archivo: dict
) -> None:
    carpeta_bronze.mkdir(parents=True, exist_ok=True)
    ahora = datetime.now().isoformat(timespec="seconds")
    with open(carpeta_bronze / NOMBRE_CONTROL, "a", encoding="utf-8") as f:
        for p in pendientes:
            registro = {
                "entrega_id": p.entrega_id,
                "archivo": p.ruta,
                "sha256": p.sha256,
                "entidad": p.entidad,
                "filas": filas_por_archivo.get(Path(p.ruta).name, 0),
                "ingerido_en": ahora,
            }
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def planear_ingesta(
    entregas: list[Entrega], ingeridos: set[tuple[str, str]]
) -> list[ArchivoPendiente]:
    """Archivos por ingerir, en el orden de las entregas. Se omite lo
    que ya se ingirio y lo que se repite entre entregas del mismo lote."""
    vistos = set(ingeridos)
    pendientes = []
    for entrega in entregas:
        for ruta, sha in sorted(entrega.archivos.items()):
            entidad = entidad_de(ruta)
            if entidad is None or (ruta, sha) in vistos:
                continue
            vistos.add((ruta, sha))
            pendientes.append(
                ArchivoPendiente(
                    entrega.entrega_id, entrega.carpeta, ruta, sha, entidad
                )
            )
    return pendientes


def bronze_sin_control(carpeta_bronze: Path) -> bool:
    """True si Bronze tiene datos pero no registro de control: viene de
    la version anterior, que sobrescribia todo en cada corrida. Ingerir
    encima duplicaria todo sin forma de saber que ya estaba."""
    if not carpeta_bronze.exists() or (carpeta_bronze / NOMBRE_CONTROL).exists():
        return False
    entidades = set(ENTIDADES_POR_ARCHIVO.values()) | {"transacciones"}
    return any((carpeta_bronze / e).exists() for e in entidades)

"""
src/common/sesiones.py

Sesiones de datos: un conjunto de fuentes crudas identificado por los
parametros que lo generaron (semilla, fecha de referencia, volumen). Con
los mismos parametros, el generador produce exactamente los mismos
archivos, asi que la sesion se puede reutilizar en vez de regenerarse,
y se puede recrear en otra maquina a partir de su id.

Estructura en disco:

    data/sesiones/<sesion_id>/        la sesion, inmutable una vez creada
        manifest.json                 parametros, conteos y checksums
        clientes.csv, cuentas.json, ...
    data/raw_sources/                 la sesion activa: lo que lee Bronze

data/raw_sources/ contiene hard links a los archivos de la sesion activa:
dos nombres para el mismo archivo en disco, sin copiar datos. Asi Bronze
sigue leyendo siempre de la misma ruta, y cambiar de sesion es
instantaneo aunque la sesion pese varios GB. Se usan hard links y no un
symlink a la carpeta porque Spark lee archivos regulares sin depender de
como el sistema de archivos resuelva enlaces simbolicos.

Que una sesion este "completa" lo define el manifest: se escribe al
final, cuando todos los archivos ya existen, y la carpeta se mueve a su
nombre definitivo con un rename atomico. Una generacion interrumpida
deja una carpeta temporal, nunca una sesion a medias con nombre valido.
"""

import hashlib
import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path

NOMBRE_MANIFEST = "manifest.json"


def id_sesion(semilla: int, fecha_referencia: date, clientes: int, meses: int) -> str:
    """Id legible y deterministico: los mismos parametros dan el mismo id.

    Ejemplo: s42_20260930_500c_12m
    """
    return f"s{semilla}_{fecha_referencia:%Y%m%d}_{clientes}c_{meses}m"


def checksum_archivo(ruta: Path) -> str:
    """SHA-256 del contenido: la huella que prueba que el archivo no cambio."""
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _archivos_de_datos(carpeta: Path) -> list[Path]:
    return sorted(
        p for p in carpeta.rglob("*") if p.is_file() and p.name != NOMBRE_MANIFEST
    )


def escribir_manifest(
    carpeta: Path,
    sesion_id: str,
    parametros: dict,
    conteos: dict,
    version_generador: str,
) -> dict:
    """Escribe manifest.json con la huella de cada archivo de la sesion."""
    manifest = {
        "sesion_id": sesion_id,
        "parametros": parametros,
        "conteos": conteos,
        "version_generador": version_generador,
        "generada_en": datetime.now().isoformat(timespec="seconds"),
        "archivos": {
            str(p.relative_to(carpeta)): checksum_archivo(p)
            for p in _archivos_de_datos(carpeta)
        },
    }
    with open(carpeta / NOMBRE_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def leer_manifest(carpeta: Path) -> dict | None:
    ruta = carpeta / NOMBRE_MANIFEST
    if not ruta.exists():
        return None
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def verificar_sesion(carpeta: Path) -> list[str]:
    """Compara los archivos contra el manifest. Devuelve la lista de
    problemas encontrados (vacia si la sesion esta integra)."""
    manifest = leer_manifest(carpeta)
    if manifest is None:
        return ["no hay manifest.json (sesion incompleta)"]

    problemas = []
    esperados = manifest["archivos"]
    presentes = {str(p.relative_to(carpeta)) for p in _archivos_de_datos(carpeta)}

    for faltante in sorted(set(esperados) - presentes):
        problemas.append(f"falta {faltante}")
    for sobrante in sorted(presentes - set(esperados)):
        problemas.append(f"archivo no registrado en el manifest: {sobrante}")
    for nombre in sorted(set(esperados) & presentes):
        if checksum_archivo(carpeta / nombre) != esperados[nombre]:
            problemas.append(f"{nombre} fue modificado despues de generarse")
    return problemas


def publicar_sesion(carpeta_temporal: Path, carpeta_final: Path) -> None:
    """Mueve una sesion recien generada a su nombre definitivo. Si ya
    existia una carpeta con ese nombre (sesion invalida que se esta
    regenerando), se reemplaza."""
    if carpeta_final.exists():
        shutil.rmtree(carpeta_final)
    os.replace(carpeta_temporal, carpeta_final)


def activar_sesion(carpeta_sesion: Path, carpeta_activa: Path) -> None:
    """Deja carpeta_activa (data/raw_sources) con el contenido de la
    sesion, via hard links. Si el sistema de archivos no los soporta,
    copia."""
    if carpeta_activa.is_symlink() or carpeta_activa.is_file():
        carpeta_activa.unlink()
    elif carpeta_activa.exists():
        # Salvaguarda: solo se borra si parece una carpeta de fuentes
        # generadas, para no borrar algo que el usuario puso ahi por error.
        es_generada = (carpeta_activa / NOMBRE_MANIFEST).exists() or (
            carpeta_activa / "clientes.csv"
        ).exists()
        if not es_generada and any(carpeta_activa.iterdir()):
            raise RuntimeError(
                f"{carpeta_activa} existe y no parece una carpeta de fuentes "
                "generadas; no se reemplaza. Revisala o usa otra ruta en --out."
            )
        shutil.rmtree(carpeta_activa)

    for origen in carpeta_sesion.rglob("*"):
        destino = carpeta_activa / origen.relative_to(carpeta_sesion)
        if origen.is_dir():
            destino.mkdir(parents=True, exist_ok=True)
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(origen, destino)
        except OSError:
            shutil.copy2(origen, destino)

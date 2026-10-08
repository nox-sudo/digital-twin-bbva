"""
tests/test_demo_respaldo.py

Pruebas de `demo.sh respaldar` y `demo.sh restaurar`. Corren el script real
sobre una copia temporal del proyecto con un data/ de mentira, sin Docker: en
una carpeta sin docker-compose.yml el script no intenta detener ni levantar
nada. Lo que verifican es la parte delicada de un respaldo: que lo copiado sea
byte a byte lo original, que restaurar lo devuelva igual, y que se niegue a
restaurar un respaldo alterado o a pisar datos existentes.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

DEMO_SH = Path(__file__).resolve().parents[1] / "demo.sh"

ARCHIVOS = {
    "sesiones/s42_demo/manifest.json": b'{"sesion_id": "s42_demo"}',
    "sesiones/s42_demo/clientes.csv": b"cliente_id\nC1\nC2\n",
    "minio/bucket/landing/entrega1/objeto.bin": bytes(range(256)),
    "minio/.minio.sys/config/config.json": b"{}",
    "calidad/incidencias/run1.txt": b"0 incidencias",
}


def _arbol(raiz: Path) -> dict:
    """Mapa ruta relativa -> contenido de todos los archivos bajo raiz."""
    return {
        str(p.relative_to(raiz)): p.read_bytes() for p in raiz.rglob("*") if p.is_file()
    }


@pytest.fixture
def proyecto(tmp_path):
    carpeta = tmp_path / "proyecto"
    (carpeta / "data").mkdir(parents=True)
    shutil.copy(DEMO_SH, carpeta / "demo.sh")
    for ruta, contenido in ARCHIVOS.items():
        destino = carpeta / "data" / ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)
    return carpeta


def _demo(proyecto: Path, *args: str):
    return subprocess.run(
        ["bash", "demo.sh", *args],
        cwd=proyecto,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(proyecto.parent / "home")},
    )


def _vaciar_data(proyecto: Path):
    for hijo in (proyecto / "data").iterdir():
        shutil.rmtree(hijo)


def test_respaldar_copia_identico_y_deja_manifiesto(proyecto, tmp_path):
    destino = tmp_path / "respaldos" / "r1"
    resultado = _demo(proyecto, "respaldar", str(destino))

    assert resultado.returncode == 0, resultado.stderr
    assert (destino / "MANIFIESTO.sha256").exists()
    copiado = {k: v for k, v in _arbol(destino).items() if k != "MANIFIESTO.sha256"}
    assert copiado == ARCHIVOS
    assert "No incluye .env" in resultado.stdout


def test_respaldar_y_restaurar_devuelve_los_datos_identicos(proyecto, tmp_path):
    destino = tmp_path / "respaldos" / "r1"
    assert _demo(proyecto, "respaldar", str(destino)).returncode == 0

    _vaciar_data(proyecto)
    resultado = _demo(proyecto, "restaurar", str(destino))

    assert resultado.returncode == 0, resultado.stderr
    assert "Restaurado y verificado" in resultado.stdout
    assert _arbol(proyecto / "data") == ARCHIVOS


def test_restaurar_sobre_directorio_vacio_no_anida_la_copia(proyecto, tmp_path):
    destino = tmp_path / "respaldos" / "r1"
    assert _demo(proyecto, "respaldar", str(destino)).returncode == 0

    _vaciar_data(proyecto)
    (proyecto / "data" / "sesiones").mkdir()
    assert _demo(proyecto, "restaurar", str(destino)).returncode == 0

    assert not (proyecto / "data" / "sesiones" / "sesiones").exists()
    assert _arbol(proyecto / "data") == ARCHIVOS


def test_restaurar_rechaza_un_respaldo_alterado(proyecto, tmp_path):
    destino = tmp_path / "respaldos" / "r1"
    assert _demo(proyecto, "respaldar", str(destino)).returncode == 0
    (destino / "sesiones" / "s42_demo" / "clientes.csv").write_bytes(b"alterado")

    _vaciar_data(proyecto)
    resultado = _demo(proyecto, "restaurar", str(destino))

    assert resultado.returncode != 0
    assert "no coincide con su manifiesto" in resultado.stderr
    assert not any((proyecto / "data").iterdir()), "no debe restaurar nada"


def test_restaurar_no_pisa_datos_existentes(proyecto, tmp_path):
    destino = tmp_path / "respaldos" / "r1"
    assert _demo(proyecto, "respaldar", str(destino)).returncode == 0

    resultado = _demo(proyecto, "restaurar", str(destino))

    assert resultado.returncode != 0
    assert "limpiar todo" in resultado.stderr
    assert _arbol(proyecto / "data") == ARCHIVOS


def test_respaldar_rechaza_un_destino_dentro_del_repo(proyecto):
    resultado = _demo(proyecto, "respaldar", str(proyecto / "respaldo_local"))

    assert resultado.returncode != 0
    assert "dentro del repo" in resultado.stderr
    assert not (proyecto / "respaldo_local").exists()


def test_respaldar_rechaza_un_destino_que_ya_existe(proyecto, tmp_path):
    destino = tmp_path / "respaldos" / "r1"
    destino.mkdir(parents=True)

    resultado = _demo(proyecto, "respaldar", str(destino))

    assert resultado.returncode != 0
    assert "ya existe" in resultado.stderr


def test_respaldar_sin_datos_falla_con_mensaje(proyecto, tmp_path):
    _vaciar_data(proyecto)
    resultado = _demo(proyecto, "respaldar", str(tmp_path / "respaldos" / "r1"))

    assert resultado.returncode != 0
    assert "No hay nada que respaldar" in resultado.stderr


@pytest.mark.parametrize(
    "argumentos, mensaje",
    [
        ([], "Uso: bash demo.sh restaurar"),
        (["/ruta/que/no/existe"], "no parece un respaldo"),
    ],
)
def test_restaurar_valida_su_argumento(proyecto, argumentos, mensaje):
    resultado = _demo(proyecto, "restaurar", *argumentos)

    assert resultado.returncode != 0
    assert mensaje in resultado.stderr

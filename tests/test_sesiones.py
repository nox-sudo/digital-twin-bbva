"""
tests/test_sesiones.py

Pruebas de las sesiones de datos reproducibles: el generador con los
mismos parametros produce los mismos archivos, reutiliza una sesion
integra, regenera una alterada, y nunca genera fechas posteriores a la
fecha de referencia. Usan volumen minimo para correr en segundos.
"""

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_synthetic_sources as generador  # noqa: E402
from src.common.sesiones import activar_sesion, verificar_sesion  # noqa: E402

PARAMS = ["--clientes", "8", "--meses", "2", "--fecha-referencia", "2026-09-30"]
SESION_ID = "s42_20260930_8c_2m"


def generar(base: Path, *extra: str) -> Path:
    """Corre el generador con carpetas propias del test; devuelve la
    carpeta de la sesion."""
    generador.main(
        [
            *PARAMS,
            "--config",
            str(base / "no-existe.yaml"),
            "--sesiones-dir",
            str(base / "sesiones"),
            "--out",
            str(base / "activa"),
            *extra,
        ]
    )
    return base / "sesiones" / SESION_ID


def checksums(carpeta_sesion: Path) -> dict:
    with open(carpeta_sesion / "manifest.json", encoding="utf-8") as f:
        return json.load(f)["archivos"]


def test_mismos_parametros_mismos_archivos(tmp_path):
    sesion_a = generar(tmp_path / "a")
    sesion_b = generar(tmp_path / "b")

    assert checksums(sesion_a) == checksums(sesion_b)


def test_semilla_distinta_datos_distintos(tmp_path):
    sesion_a = generar(tmp_path / "a")
    generar(tmp_path / "b", "--semilla", "7")
    sesion_b = tmp_path / "b" / "sesiones" / "s7_20260930_8c_2m"

    assert checksums(sesion_a)["clientes.csv"] != checksums(sesion_b)["clientes.csv"]


def test_segunda_corrida_reutiliza_sin_regenerar(tmp_path, capsys):
    sesion = generar(tmp_path)
    generada_en = json.loads((sesion / "manifest.json").read_text())["generada_en"]
    capsys.readouterr()

    generar(tmp_path)

    assert "se reutiliza" in capsys.readouterr().out
    assert (
        json.loads((sesion / "manifest.json").read_text())["generada_en"] == generada_en
    )


def test_sesion_alterada_se_detecta_y_se_regenera(tmp_path, capsys):
    sesion = generar(tmp_path)
    originales = checksums(sesion)
    (sesion / "clientes.csv").write_text("cliente_id\nCLI-999999\n")
    assert any("clientes.csv" in p for p in verificar_sesion(sesion))
    capsys.readouterr()

    generar(tmp_path)

    assert "no esta integra" in capsys.readouterr().out
    assert verificar_sesion(sesion) == []
    assert checksums(sesion) == originales


def test_carpeta_activa_tiene_los_archivos_de_la_sesion(tmp_path):
    sesion = generar(tmp_path)
    activa = tmp_path / "activa"

    assert (activa / "manifest.json").exists()
    for nombre in checksums(sesion):
        assert (activa / nombre).read_bytes() == (sesion / nombre).read_bytes()


def test_no_hay_fechas_posteriores_a_la_referencia(tmp_path):
    generar(tmp_path)
    activa = tmp_path / "activa"
    transacciones = pd.concat(
        pd.read_csv(f) for f in (activa / "transacciones").glob("*.csv")
    )
    clientes = pd.read_csv(activa / "clientes.csv")

    assert transacciones["fecha"].max() <= "2026-09-30"
    assert clientes["fecha_alta"].max() <= "2026-09-30"


@pytest.mark.parametrize(
    "fecha_ref, esperado",
    [
        # Fin de mes: septiembre esta completo y se incluye.
        (date(2026, 9, 30), [(2026, 9), (2026, 8)]),
        # A mitad de mes: octubre esta en curso y se excluye.
        (date(2026, 10, 1), [(2026, 9), (2026, 8)]),
        # Cruce de anio.
        (date(2026, 1, 15), [(2025, 12), (2025, 11)]),
    ],
)
def test_meses_a_generar(fecha_ref, esperado):
    assert generador.meses_a_generar(fecha_ref, 2) == esperado


def test_activar_no_borra_una_carpeta_ajena(tmp_path):
    sesion = generar(tmp_path)
    ajena = tmp_path / "ajena"
    ajena.mkdir()
    (ajena / "notas_importantes.txt").write_text("no borrar")

    with pytest.raises(RuntimeError, match="no parece una carpeta de fuentes"):
        activar_sesion(sesion, ajena)
    assert (ajena / "notas_importantes.txt").exists()


def test_misma_sesion_en_cualquier_zona_horaria(tmp_path):
    """La sesion no depende de la zona horaria del proceso. Antes, Faker
    convertia las fechas anteriores a 1970 con la zona local, y una sesion
    generada en una Mac (UTC-7) y en el worker (UTC) diferia en fechas de
    nacimiento, CURP y RFC. Cada generacion corre en su propio proceso
    porque la zona se lee al arrancar."""
    import subprocess

    raiz = Path(__file__).resolve().parents[1]
    archivos = {}
    for zona in ("UTC", "America/Mazatlan", "Asia/Tokyo"):
        destino = tmp_path / zona.replace("/", "_")
        subprocess.run(
            [
                sys.executable,
                "generate_synthetic_sources.py",
                "--clientes",
                "60",
                "--meses",
                "1",
                "--fecha-referencia",
                "2026-09-30",
                "--config",
                str(tmp_path / "no-existe.yaml"),
                "--sesiones-dir",
                str(destino / "sesiones"),
                "--out",
                str(destino / "activa"),
            ],
            cwd=raiz,
            env={**__import__("os").environ, "TZ": zona},
            check=True,
            capture_output=True,
        )
        archivos[zona] = checksums(destino / "sesiones" / "s42_20260930_60c_1m")
        clientes = pd.read_csv(destino / "activa" / "clientes.csv")

    # La prueba solo sirve si hay fechas anteriores a 1970 (timestamp negativo).
    assert (pd.to_datetime(clientes["fecha_nacimiento"]) < "1970-01-01").any()
    assert archivos["UTC"] == archivos["America/Mazatlan"] == archivos["Asia/Tokyo"]

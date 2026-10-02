"""
tests/test_landing.py

Pruebas de la zona de aterrizaje y de la planeacion de ingesta
incremental. S3 se simula en memoria con moto, asi que no hace falta
MinIO ni red.
"""

import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_synthetic_sources as generador  # noqa: E402
from src.bronze.control import (  # noqa: E402
    ArchivoPendiente,
    Entrega,
    anotar_ingeridos,
    bronze_sin_control,
    entidad_de,
    leer_control,
    planear_ingesta,
)
from src.common.landing import (  # noqa: E402
    BUCKET_DEFAULT,
    descargar_entrega,
    listar_entregas,
    publicar_entrega,
)


def generar(base: Path, meses_adicionales: int = 0) -> Path:
    generador.main(
        [
            "--clientes", "10", "--meses", "2", "--fecha-referencia", "2026-09-30",
            "--meses-adicionales", str(meses_adicionales),
            "--config", str(base / "no-existe.yaml"),
            "--sesiones-dir", str(base / "sesiones"),
            "--out", str(base / "activa"),
        ]
    )  # fmt: skip
    return base / "activa"


@pytest.fixture
def s3():
    with mock_aws():
        cliente = boto3.client("s3", region_name="us-east-1")
        cliente.create_bucket(Bucket=BUCKET_DEFAULT)
        yield cliente


# --- Sesiones extensibles ----------------------------------------------------


def test_entrega_nueva_no_altera_meses_anteriores(tmp_path):
    import json

    generar(tmp_path)
    base = json.loads((tmp_path / "activa/manifest.json").read_text())["archivos"]
    generar(tmp_path, meses_adicionales=1)
    e1 = json.loads((tmp_path / "activa/manifest.json").read_text())["archivos"]

    assert all(e1[ruta] == sha for ruta, sha in base.items())
    assert set(e1) - set(base) == {"transacciones/transacciones_2026_10.csv"}


# --- Landing zone --------------------------------------------------------------


def test_publicar_solo_sube_lo_nuevo(s3, tmp_path):
    publicar_entrega(s3, generar(tmp_path))
    publicar_entrega(s3, generar(tmp_path, meses_adicionales=1))

    entregas = listar_entregas(s3)
    assert [m["entrega_id"] for m in entregas] == [
        "s42_20260930_10c_2m",
        "s42_20260930_10c_2m_e1",
    ]
    assert len(entregas[0]["archivos"]) == 6
    assert list(entregas[1]["archivos"]) == ["transacciones/transacciones_2026_10.csv"]


def test_entrega_publicada_es_inmutable(s3, tmp_path):
    sesion = generar(tmp_path)
    assert publicar_entrega(s3, sesion) is not None
    assert publicar_entrega(s3, sesion) is None
    assert len(listar_entregas(s3)) == 1


def test_descarga_verifica_integridad(s3, tmp_path):
    manifest = publicar_entrega(s3, generar(tmp_path))
    llave = f"landing/entregas/{manifest['entrega_id']}/clientes.csv"
    s3.put_object(Bucket=BUCKET_DEFAULT, Key=llave, Body=b"alterado")

    with pytest.raises(RuntimeError, match="Integridad"):
        descargar_entrega(s3, manifest, tmp_path / "staging")


def test_descarga_reproduce_los_archivos(s3, tmp_path):
    sesion = generar(tmp_path)
    manifest = publicar_entrega(s3, sesion)
    carpeta = descargar_entrega(s3, manifest, tmp_path / "staging")

    for ruta in manifest["archivos"]:
        assert (carpeta / ruta).read_bytes() == (sesion / ruta).read_bytes()


def test_entrega_sin_manifest_no_existe_para_quien_lee(s3):
    # Publicacion interrumpida: archivos subidos, manifest nunca escrito.
    s3.put_object(
        Bucket=BUCKET_DEFAULT, Key="landing/entregas/a_medias/clientes.csv", Body=b"x"
    )
    assert listar_entregas(s3) == []


# --- Planeacion de ingesta incremental -----------------------------------------


def test_entidad_de():
    assert entidad_de("clientes.csv") == "clientes"
    assert entidad_de("transacciones/transacciones_2026_10.csv") == "transacciones"
    assert entidad_de("manifest.json") is None


def test_planear_omite_lo_ingerido_y_repetido(tmp_path):
    e0 = Entrega("e0", tmp_path, {"clientes.csv": "a", "transacciones/t1.csv": "b"})
    e1 = Entrega("e1", tmp_path, {"clientes.csv": "a", "transacciones/t2.csv": "c"})

    primera = planear_ingesta([e0, e1], ingeridos=set())
    assert [(p.entrega_id, p.ruta) for p in primera] == [
        ("e0", "clientes.csv"),
        ("e0", "transacciones/t1.csv"),
        ("e1", "transacciones/t2.csv"),
    ]
    assert planear_ingesta([e0, e1], {(p.ruta, p.sha256) for p in primera}) == []


def test_control_registra_y_relee(tmp_path):
    pendientes = [ArchivoPendiente("e0", tmp_path, "clientes.csv", "a", "clientes")]
    anotar_ingeridos(tmp_path / "bronze", pendientes, {"clientes.csv": 10})

    assert leer_control(tmp_path / "bronze") == {("clientes.csv", "a")}


def test_detecta_bronze_de_la_version_anterior(tmp_path):
    bronze = tmp_path / "bronze"
    (bronze / "clientes").mkdir(parents=True)
    assert bronze_sin_control(bronze)

    anotar_ingeridos(bronze, [], {})
    assert not bronze_sin_control(bronze)

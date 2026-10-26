"""
tests/test_secretos.py

Pruebas de src/common/secretos.py, que entrega la sal de los hashes de PII.

Lo que importa proteger: (1) la variable de entorno gana sobre .env, porque
es lo que inyecta Docker; (2) sin secreto se falla con un mensaje que dice
como arreglarlo, nunca con un valor por default, porque una sal conocida
equivale a no tener sal.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.secretos import obtener_secreto  # noqa: E402

NOMBRE = "SECRETO_DE_PRUEBA"


@pytest.fixture(autouse=True)
def sin_variable(monkeypatch):
    monkeypatch.delenv(NOMBRE, raising=False)


def _env(tmp_path, contenido: str):
    ruta = tmp_path / ".env"
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def test_lee_de_variable_de_entorno(monkeypatch, tmp_path):
    monkeypatch.setenv(NOMBRE, "desde-entorno")
    assert obtener_secreto(NOMBRE, ruta_env=tmp_path / "no-existe") == "desde-entorno"


def test_entorno_gana_sobre_archivo(monkeypatch, tmp_path):
    monkeypatch.setenv(NOMBRE, "desde-entorno")
    ruta = _env(tmp_path, f"{NOMBRE}=desde-archivo\n")
    assert obtener_secreto(NOMBRE, ruta_env=ruta) == "desde-entorno"


def test_lee_de_archivo_env(tmp_path):
    ruta = _env(tmp_path, f"OTRA=x\n{NOMBRE}=desde-archivo\n")
    assert obtener_secreto(NOMBRE, ruta_env=ruta) == "desde-archivo"


def test_ignora_comentarios_lineas_vacias_y_espacios(tmp_path):
    ruta = _env(tmp_path, f"# comentario\n\n  {NOMBRE} =  abc123  \nlinea sin igual\n")
    assert obtener_secreto(NOMBRE, ruta_env=ruta) == "abc123"


def test_conserva_signos_igual_dentro_del_valor(tmp_path):
    # Un valor en base64 puede terminar en "=". Solo se parte en el primero.
    ruta = _env(tmp_path, f"{NOMBRE}=abc==\n")
    assert obtener_secreto(NOMBRE, ruta_env=ruta) == "abc=="


def test_variable_vacia_cae_al_archivo(monkeypatch, tmp_path):
    monkeypatch.setenv(NOMBRE, "")
    ruta = _env(tmp_path, f"{NOMBRE}=desde-archivo\n")
    assert obtener_secreto(NOMBRE, ruta_env=ruta) == "desde-archivo"


@pytest.mark.parametrize("contenido", [None, "", f"{NOMBRE}=\n", "OTRA=x\n"])
def test_sin_secreto_falla_con_mensaje_accionable(tmp_path, contenido):
    ruta = tmp_path / ".env" if contenido is None else _env(tmp_path, contenido)
    with pytest.raises(RuntimeError) as error:
        obtener_secreto(NOMBRE, ruta_env=ruta)
    assert NOMBRE in str(error.value)
    assert "setup.sh" in str(error.value)

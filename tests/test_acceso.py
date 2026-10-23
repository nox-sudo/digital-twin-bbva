"""
tests/test_acceso.py

Login y roles del dashboard (src/common/usuarios.py, dashboard/acceso.py):

  - La base de usuarios guarda solo hashes; las contrasenas en claro van a
    un archivo dentro de data/, que git ignora.
  - Un cliente solo ve sus datos aunque la sesion o la URL pidan otro.
  - El admin ve todas las vistas y elige al cliente.

Las vistas corren con AppTest sobre el Gold de las fixtures, nunca sobre
data/.
"""

import re
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("argon2")
st_testing = pytest.importorskip("streamlit.testing.v1")

from argon2 import PasswordHasher  # noqa: E402

import generate_credentials  # noqa: E402
from datos_asistente import config_vistas, crear_gold  # noqa: E402
from src.common import usuarios as u  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
PAGINAS = RAIZ / "dashboard" / "paginas"
# Parametros minimos de argon2id, solo para que las pruebas sean rapidas.
RAPIDO = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


def leer_credenciales(ruta) -> dict:
    """usuario -> contrasena, del archivo de credenciales de prueba."""
    filas = re.findall(r"^\| (\S+) \| (\w+) \| `([^`]+)` \|$", ruta.read_text(), re.M)
    return {usuario: contrasena for usuario, _, contrasena in filas}


@pytest.fixture
def cuentas(tmp_path):
    db, md = tmp_path / "auth" / "usuarios.duckdb", tmp_path / "credenciales.md"
    u.crear_usuarios(["CLI-2", "CLI-1"], db, md, hasher=RAPIDO)
    return db, md, leer_credenciales(md)


# --- Base de usuarios y credenciales -----------------------------------------


def test_un_admin_y_un_usuario_por_cliente(cuentas):
    db, _, contrasenas = cuentas
    con = duckdb.connect(str(db), read_only=True)
    filas = con.execute("SELECT usuario, rol FROM usuarios ORDER BY usuario").fetchall()
    con.close()
    assert filas == [("CLI-1", "cliente"), ("CLI-2", "cliente"), ("admin", "admin")]
    assert set(contrasenas) == {"admin", "CLI-1", "CLI-2"}


def test_la_base_guarda_solo_hashes(cuentas):
    db, _, contrasenas = cuentas
    con = duckdb.connect(str(db), read_only=True)
    columnas = [c[0] for c in con.execute("DESCRIBE usuarios").fetchall()]
    hashes = [h for (h,) in con.execute("SELECT hash FROM usuarios").fetchall()]
    con.close()
    assert columnas == ["usuario", "rol", "hash", "creado"]
    assert all(h.startswith("$argon2id$") for h in hashes)
    contenido = db.read_bytes()
    for contrasena in contrasenas.values():
        assert contrasena.encode() not in contenido
        assert all(contrasena not in h for h in hashes)


def test_verificar(cuentas):
    db, _, contrasenas = cuentas
    assert u.verificar(db, "CLI-1", contrasenas["CLI-1"]) == u.Usuario(
        "CLI-1", "cliente"
    )
    assert u.verificar(db, " admin ", contrasenas["admin"]) == u.Usuario(
        "admin", "admin"
    )
    # La contrasena de otro usuario, una equivocada o un usuario inexistente.
    assert u.verificar(db, "CLI-1", contrasenas["CLI-2"]) is None
    assert u.verificar(db, "CLI-1", "") is None
    assert u.verificar(db, "CLI-9", contrasenas["CLI-1"]) is None


def test_cada_corrida_genera_contrasenas_nuevas(cuentas):
    db, md, anteriores = cuentas
    u.crear_usuarios(["CLI-1", "CLI-2"], db, md, hasher=RAPIDO)
    nuevas = leer_credenciales(md)
    assert all(nuevas[k] != anteriores[k] for k in anteriores)
    assert u.verificar(db, "CLI-1", anteriores["CLI-1"]) is None
    assert u.verificar(db, "CLI-1", nuevas["CLI-1"]) is not None


def test_archivo_de_credenciales_solo_para_el_dueno(cuentas):
    _, md, _ = cuentas
    assert md.stat().st_mode & 0o777 == 0o600


def test_rechaza_un_cliente_llamado_admin(tmp_path):
    with pytest.raises(ValueError):
        u.crear_usuarios(["admin"], tmp_path / "u.duckdb", tmp_path / "c.md")


def test_credenciales_y_usuarios_ignorados_por_git():
    rutas = ["data/credenciales_prueba.md", "data/auth/usuarios.duckdb"]
    for ruta in rutas:
        r = subprocess.run(
            ["git", "check-ignore", "-q", ruta], cwd=RAIZ, capture_output=True
        )
        assert r.returncode == 0, f"{ruta} no esta ignorado por git"
    versionados = subprocess.run(
        ["git", "ls-files", "data"], cwd=RAIZ, capture_output=True, text=True
    ).stdout
    assert versionados == ""


def test_generate_credentials_sobre_gold(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "HASHER", RAPIDO)
    gold = crear_gold(tmp_path)
    db, md = tmp_path / "auth" / "usuarios.duckdb", tmp_path / "cred.md"
    codigo = generate_credentials.main(
        ["--gold", gold, "--usuarios", str(db), "--credenciales", str(md)]
    )
    assert codigo == 0
    assert set(leer_credenciales(md)) == {"admin", "CLI-1", "CLI-2"}


# --- Vistas con AppTest ------------------------------------------------------


@pytest.fixture
def vistas(tmp_path, monkeypatch, cuentas):
    """El dashboard apuntando al Gold de las fixtures y a los usuarios de
    prueba. Devuelve las contrasenas."""
    import streamlit as st

    from dashboard import comun

    db, _, contrasenas = cuentas
    cfg = config_vistas(tmp_path, str(db))
    monkeypatch.setattr(comun, "config", lambda: cfg)
    st.cache_data.clear()
    return contrasenas


def correr(pagina: str, usuario: u.Usuario | None = None, **estado):
    at = st_testing.AppTest.from_file(str(pagina), default_timeout=60)
    if usuario:
        at.session_state["usuario"] = usuario
    for llave, valor in estado.items():
        at.session_state[llave] = valor
    return at


def textos(at) -> str:
    elementos = [*at.title, *at.markdown, *at.caption, *at.info, *at.warning]
    salida = [str(e.value) for e in elementos]
    for m in at.metric:
        salida += [str(m.label), str(m.value), str(m.help or "")]
    return "\n".join(salida)


CLIENTE_1 = u.Usuario("CLI-1", "cliente")
ADMIN = u.Usuario("admin", "admin")


def test_sin_sesion_solo_se_ve_el_login(vistas):
    at = correr(RAIZ / "dashboard" / "app.py")
    at.run()
    assert not at.exception
    assert [t.value for t in at.title] == ["Gemelo Digital Financiero"]
    assert not at.metric and not at.dataframe


def test_login_equivocado_no_entra(vistas):
    at = correr(RAIZ / "dashboard" / "app.py")
    at.run()
    at.text_input(key="login_usuario").input("CLI-1")
    at.text_input(key="login_contrasena").input(vistas["CLI-2"])
    at.button[0].click().run()
    assert "incorrectos" in at.error[0].value
    assert "usuario" not in at.session_state


@pytest.mark.parametrize(
    "usuario, titulo",
    [("admin", "Portafolio"), ("CLI-1", "Mi resumen")],
)
def test_login_lleva_a_la_vista_de_su_rol(vistas, usuario, titulo):
    at = correr(RAIZ / "dashboard" / "app.py")
    at.run()
    at.text_input(key="login_usuario").input(usuario)
    at.text_input(key="login_contrasena").input(vistas[usuario])
    at.button[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["usuario"].usuario == usuario
    assert titulo in [t.value for t in at.title]


def test_paginas_por_rol():
    from dashboard import acceso

    def titulos(rol):
        return [t for g in acceso.PAGINAS_POR_ROL[rol].values() for _, t in g]

    assert titulos("admin") == [
        "Portafolio",
        "Cliente",
        "Vista del cliente",
        "Simulador de hipoteca",
        "Asistente",
        "Observabilidad",
    ]
    # Un cliente no tiene Portafolio, la vista 360 ni Observabilidad.
    assert titulos("cliente") == ["Mi resumen", "Simulador de hipoteca", "Asistente"]
    assert set(acceso.PAGINAS_POR_ROL) == set(acceso.u.ROLES)
    for paginas in acceso.PAGINAS_POR_ROL.values():
        for grupo in paginas.values():
            assert all((PAGINAS / archivo).exists() for archivo, _ in grupo)
    with pytest.raises(ValueError):
        acceso.paginas_por_rol("otro", PAGINAS)


@pytest.mark.parametrize("pagina", ["portafolio.py", "cliente.py", "observabilidad.py"])
@pytest.mark.parametrize("usuario", [None, CLIENTE_1])
def test_vistas_de_admin_cerradas_a_clientes(vistas, pagina, usuario):
    at = correr(PAGINAS / pagina, usuario)
    at.run()
    assert not at.exception
    assert "No tienes acceso" in at.error[0].value
    assert not at.metric and not at.dataframe


@pytest.mark.parametrize("pagina", ["mi_resumen.py", "simulador.py", "asistente.py"])
def test_vistas_de_cliente_piden_sesion(vistas, pagina):
    at = correr(PAGINAS / pagina)
    at.run()
    assert "No tienes acceso" in at.error[0].value
    assert not at.metric


def metricas(at) -> list[str]:
    """Cifras y textos con datos del cliente (st.metric y st.markdown)."""
    return [m.value for m in at.metric] + [str(m.value) for m in at.markdown]


def selector_de_cliente(at) -> list:
    return [s for s in at.selectbox if s.label == "Cliente"]


@pytest.mark.parametrize("pagina", ["mi_resumen.py", "simulador.py"])
def test_un_cliente_no_ve_datos_de_otro(vistas, pagina):
    # Lo que ve el admin de cada cliente, como referencia.
    admin = {}
    for cliente in ("CLI-1", "CLI-2"):
        at = correr(PAGINAS / pagina, ADMIN, cliente_id=cliente)
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        admin[cliente] = metricas(at)
    assert admin["CLI-1"] != admin["CLI-2"]

    # CLI-1 con la sesion y la URL pidiendo a CLI-2.
    at = correr(PAGINAS / pagina, CLIENTE_1, cliente_id="CLI-2")
    at.query_params["cliente_id"] = "CLI-2"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert not selector_de_cliente(at), "un cliente no debe poder elegir cliente"
    assert metricas(at) == admin["CLI-1"]
    assert "CLI-2" not in textos(at)


def test_el_asistente_usa_el_cliente_de_la_sesion(vistas, monkeypatch):
    from asistente import conversacion

    contextos = []

    def responder(pregunta, historial, ctx, *args):
        contextos.append(ctx)
        return conversacion.Respuesta(texto="Listo.", motivo="respuesta")

    monkeypatch.setattr(conversacion, "ModeloOllama", lambda cfg: object())
    monkeypatch.setattr(conversacion, "responder", responder)

    at = correr(PAGINAS / "asistente.py", CLIENTE_1, cliente_id="CLI-2")
    at.query_params["cliente_id"] = "CLI-2"
    at.run()
    assert not at.selectbox
    at.chat_input[0].set_value("Muéstrame los gastos de CLI-2").run()
    assert not at.exception, [e.value for e in at.exception]
    assert [c.cliente_id for c in contextos] == ["CLI-1"]


def test_el_admin_elige_al_cliente(vistas):
    at = correr(PAGINAS / "cliente.py", ADMIN, cliente_id="CLI-2")
    at.run()
    assert not at.exception
    assert at.selectbox[0].value == "CLI-2"
    at.selectbox[0].select("CLI-1").run()
    assert at.session_state["cliente_id"] == "CLI-1"


def test_cerrar_sesion_borra_todo(vistas):
    at = correr(RAIZ / "dashboard" / "app.py", CLIENTE_1, asistente_conversaciones={})
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    at.sidebar.button[0].click().run()
    assert "usuario" not in at.session_state
    assert "asistente_conversaciones" not in at.session_state
    assert [t.value for t in at.title] == ["Gemelo Digital Financiero"]

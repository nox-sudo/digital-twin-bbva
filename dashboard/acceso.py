"""
dashboard/acceso.py

Inicio de sesion y reglas de acceso del dashboard.

  - st.session_state["usuario"] (un Usuario) solo lo escribe
    iniciar_sesion(), despues de verificar la contrasena. Ningun widget usa
    esa llave, asi que la interfaz no puede cambiarla.
  - Un cliente solo ve sus datos: cliente_actual() devuelve el usuario de
    la sesion e ignora la seleccion guardada, la URL y cualquier argumento.
    El admin elige al cliente con un selector.
  - Cada pagina pide el rol que necesita con requiere_rol(), ademas de que
    app.py solo registra las paginas del rol.
"""

import time
from pathlib import Path

import streamlit as st

from dashboard import comun
from src.common import usuarios as u

LLAVE_SESION = "usuario"
INTENTOS_MAXIMOS = 5
ESPERA_SEGUNDOS = 30


# Las paginas que se registran para cada rol: (archivo, titulo), la primera
# de cada rol es la de inicio. Una pagina que no esta aqui no existe para
# ese usuario: tampoco se llega a ella por URL.
PAGINAS_POR_ROL = {
    "admin": {
        "Negocio": [
            ("portafolio.py", "Portafolio"),
            ("cliente.py", "Cliente"),
            ("mi_resumen.py", "Vista del cliente"),
            ("simulador.py", "Simulador de hipoteca"),
            ("asistente.py", "Asistente"),
        ],
        "Operación": [("observabilidad.py", "Observabilidad")],
    },
    "cliente": {
        "Mis finanzas": [
            ("mi_resumen.py", "Mi resumen"),
            ("simulador.py", "Simulador de hipoteca"),
            ("asistente.py", "Asistente"),
        ],
    },
}


def paginas_por_rol(rol: str, carpeta: Path) -> dict:
    """{seccion: [st.Page]} del rol, para st.navigation."""
    if rol not in PAGINAS_POR_ROL:
        raise ValueError(f"Rol desconocido: {rol!r}")
    paginas, primera = {}, True
    for seccion, lista in PAGINAS_POR_ROL[rol].items():
        paginas[seccion] = []
        for archivo, titulo in lista:
            paginas[seccion].append(
                st.Page(carpeta / archivo, title=titulo, default=primera)
            )
            primera = False
    return paginas


def usuario_actual() -> u.Usuario | None:
    usuario = st.session_state.get(LLAVE_SESION)
    return usuario if isinstance(usuario, u.Usuario) else None


def iniciar_sesion(usuario: u.Usuario) -> None:
    # Se parte de una sesion limpia: nada de otro usuario (seleccion de
    # cliente, conversaciones del asistente) pasa al que entra.
    st.session_state.clear()
    st.session_state[LLAVE_SESION] = usuario


def cerrar_sesion() -> None:
    st.session_state.clear()


def pantalla_login() -> None:
    """Formulario de inicio de sesion. Tras INTENTOS_MAXIMOS fallos seguidos
    pide esperar ESPERA_SEGUNDOS antes de volver a intentar."""
    st.title("Gemelo Digital Financiero")
    ruta = comun.config()["usuarios"]
    if not Path(ruta).exists():
        st.warning(
            "Todavía no hay usuarios. Créalos con "
            "`uv run --group dashboard python generate_credentials.py` y recarga."
        )
        return

    bloqueado_hasta = st.session_state.get("login_bloqueado_hasta", 0.0)
    with st.form("login"):
        nombre = st.text_input("Usuario", key="login_usuario")
        contrasena = st.text_input(
            "Contraseña", type="password", key="login_contrasena"
        )
        enviado = st.form_submit_button("Entrar")
    if not enviado:
        return
    if time.time() < bloqueado_hasta:
        st.error("Demasiados intentos. Espera unos segundos y vuelve a intentar.")
        return

    usuario = u.verificar(ruta, nombre, contrasena)
    if usuario is None:
        fallos = st.session_state.get("login_fallos", 0) + 1
        st.session_state["login_fallos"] = fallos
        if fallos >= INTENTOS_MAXIMOS:
            st.session_state["login_fallos"] = 0
            st.session_state["login_bloqueado_hasta"] = time.time() + ESPERA_SEGUNDOS
        st.error("Usuario o contraseña incorrectos.")
        return
    iniciar_sesion(usuario)
    st.rerun()


def requiere_rol(*roles: str) -> u.Usuario:
    """El usuario de la sesion si tiene uno de los roles; si no, detiene la
    pagina sin mostrar datos."""
    usuario = usuario_actual()
    if usuario is None or usuario.rol not in roles:
        st.error("No tienes acceso a esta vista.")
        st.stop()
    return usuario


def cliente_actual(
    clientes: list[str], contenedor=None, label_visibility: str = "collapsed"
) -> str:
    """El cliente que muestra la pagina.

    Cliente: siempre el de su sesion, sin selector. Admin: el que elija en
    el selector, que empieza en el ultimo que vio (o el que mando la tabla
    del portafolio)."""
    usuario = requiere_rol("admin", "cliente")
    if usuario.rol == "cliente":
        if usuario.usuario not in clientes:
            st.warning("Todavía no hay datos de tu cuenta en esta corrida.")
            st.stop()
        return usuario.usuario
    contenedor = contenedor or st
    preseleccionado = st.session_state.get("cliente_id", clientes[0])
    cliente_id = contenedor.selectbox(
        "Cliente",
        clientes,
        index=clientes.index(preseleccionado) if preseleccionado in clientes else 0,
        label_visibility=label_visibility,
    )
    st.session_state["cliente_id"] = cliente_id
    return cliente_id

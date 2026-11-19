"""
experimentos/capturas_ux.py

Capturas de cada pantalla del dashboard para la auditoria de usabilidad
(docs/ux/auditoria.md). Recorre la app como lo haria una persona: entra
como admin, abre cada vista, sale y entra como cliente. Cada pantalla se
guarda completa (no solo lo que cabe en la ventana) en dos anchos:
escritorio 1440 px y laptop 1280 px.

Las contrasenas se leen de data/credenciales_prueba.md (generate_credentials.py)
y no se imprimen. Necesita el dashboard corriendo y, para la respuesta del
asistente, Ollama. No corre en CI. Uso:

    uv run python experimentos/capturas_ux.py --url http://localhost:8502 \\
        --salida docs/ux/antes
    # Estado sin datos: una copia del repo sin Gold, solo con usuarios
    uv run python experimentos/capturas_ux.py --url http://localhost:8503 \\
        --salida docs/ux/antes --solo-sin-datos
"""

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

RAIZ = Path(__file__).resolve().parents[1]
ANCHOS = {"escritorio": 1440, "laptop": 1280, "celular": 390}
ALTO_VENTANA = 900
CLIENTE_DEMO = "CLI-000283"


def credenciales(ruta: Path) -> dict[str, str]:
    """{usuario: contrasena} de la tabla del archivo de credenciales."""
    filas = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        partes = [p.strip().strip("`") for p in linea.strip().strip("|").split("|")]
        if len(partes) == 3 and partes[1] in ("admin", "cliente"):
            filas[partes[0]] = partes[2]
    return filas


def esperar(page: Page, extra: float = 1.5) -> None:
    """Hasta que Streamlit termina de correr el script y las graficas pintan."""
    page.wait_for_load_state("networkidle")
    try:
        page.wait_for_selector(
            "[data-testid='stStatusWidget']", state="detached", timeout=120_000
        )
    except Exception:
        pass
    time.sleep(extra)


def capturar(page: Page, salida: Path, nombre: str, ancho: int) -> None:
    """Pantalla completa: Streamlit hace scroll dentro de su contenedor, asi
    que se agranda la ventana al alto del contenido antes de la captura."""
    alto = page.evaluate("""() => {
            let alto = window.innerHeight;
            for (const el of document.querySelectorAll('*')) {
                const estilo = getComputedStyle(el).overflowY;
                if (estilo === 'auto' || estilo === 'scroll') {
                    alto = Math.max(alto, el.scrollHeight);
                }
            }
            return alto;
        }""")
    page.set_viewport_size({"width": ancho, "height": int(min(alto, 12000))})
    time.sleep(1.0)
    ruta = salida / f"{nombre}.png"
    page.screenshot(path=str(ruta))
    page.set_viewport_size({"width": ancho, "height": ALTO_VENTANA})
    print(f"  {ruta.relative_to(RAIZ) if ruta.is_relative_to(RAIZ) else ruta}")


def abrir_menu(page: Page) -> None:
    """En pantallas angostas la barra lateral empieza cerrada."""
    boton = page.locator("[data-testid='stExpandSidebarButton']")
    if boton.count() and boton.first.is_visible():
        boton.first.click()
        time.sleep(0.8)


def cerrar_menu(page: Page) -> None:
    """En pantallas angostas el menu tapa la vista: se cierra para capturar."""
    cerrar = page.locator("[data-testid='stSidebarCollapseButton'] button")
    if (
        page.viewport_size["width"] < 768
        and cerrar.count()
        and cerrar.first.is_visible()
    ):
        cerrar.first.click()
        time.sleep(0.8)


def ir_a(page: Page, titulo: str) -> None:
    abrir_menu(page)
    page.locator("[data-testid='stSidebarNav']").get_by_role(
        "link", name=titulo, exact=True
    ).click()
    esperar(page)
    cerrar_menu(page)


def cerrar_sesion(page: Page) -> None:
    abrir_menu(page)
    page.get_by_role("button", name="Cerrar sesión").click()
    esperar(page)


def entrar(page: Page, url: str, usuario: str, contrasena: str) -> None:
    page.goto(url)
    esperar(page)
    page.get_by_label("Usuario").fill(usuario)
    page.get_by_label("Contraseña").fill(contrasena)
    page.get_by_role("button", name="Entrar").click()
    esperar(page, 2.5)
    cerrar_menu(page)


def elegir_cliente(page: Page, cliente_id: str) -> None:
    caja = page.locator("[data-testid='stSelectbox']").first
    caja.click()
    page.keyboard.type(cliente_id)
    page.keyboard.press("Enter")
    esperar(page)


def recorrido(
    page: Page,
    url: str,
    salida: Path,
    ancho: str,
    claves: dict,
    con_respuesta: bool = True,
) -> None:
    px = ANCHOS[ancho]
    page.set_viewport_size({"width": px, "height": ALTO_VENTANA})

    page.goto(url)
    esperar(page)
    capturar(page, salida, f"01-login-{ancho}", px)

    page.get_by_label("Usuario").fill("admin")
    page.get_by_label("Contraseña").fill("contrasena-incorrecta")
    page.get_by_role("button", name="Entrar").click()
    esperar(page)
    capturar(page, salida, f"02-login-error-{ancho}", px)

    entrar(page, url, "admin", claves["admin"])
    capturar(page, salida, f"03-admin-portafolio-{ancho}", px)
    ir_a(page, "Cliente")
    elegir_cliente(page, CLIENTE_DEMO)
    capturar(page, salida, f"04-admin-cliente-{ancho}", px)
    ir_a(page, "Vista del cliente")
    capturar(page, salida, f"05-admin-vista-cliente-{ancho}", px)
    ir_a(page, "Simulador de hipoteca")
    esperar(page, 3)
    capturar(page, salida, f"06-admin-simulador-{ancho}", px)
    ir_a(page, "Asistente")
    capturar(page, salida, f"07-admin-asistente-{ancho}", px)
    ir_a(page, "Observabilidad")
    esperar(page, 3)
    capturar(page, salida, f"08-admin-observabilidad-{ancho}", px)
    cerrar_sesion(page)

    entrar(page, url, CLIENTE_DEMO, claves[CLIENTE_DEMO])
    capturar(page, salida, f"09-cliente-mi-resumen-{ancho}", px)
    ir_a(page, "Asistente")
    capturar(page, salida, f"10-cliente-asistente-{ancho}", px)
    if ancho == "escritorio" and con_respuesta:
        # Una respuesta real del modelo (una sola vez: tarda y no cambia
        # con el ancho de la ventana), con la primera pregunta de ejemplo.
        page.locator("[data-testid='stMain'] button").filter(has_text="¿").first.click()
        page.wait_for_selector("[data-testid='stChatMessage']", timeout=30_000)
        time.sleep(1)
        capturar(page, salida, f"11-cliente-asistente-pensando-{ancho}", px)
        page.locator("[data-testid='stSpinner']").wait_for(
            state="detached", timeout=300_000
        )
        esperar(page, 2)
        capturar(page, salida, f"12-cliente-asistente-respuesta-{ancho}", px)
    cerrar_sesion(page)


def sin_datos(page: Page, url: str, salida: Path, ancho: str, claves: dict) -> None:
    px = ANCHOS[ancho]
    page.set_viewport_size({"width": px, "height": ALTO_VENTANA})
    entrar(page, url, "admin", claves["admin"])
    capturar(page, salida, f"13-sin-datos-portafolio-{ancho}", px)
    entrar(page, url, CLIENTE_DEMO, claves[CLIENTE_DEMO])
    capturar(page, salida, f"14-sin-datos-mi-resumen-{ancho}", px)


def main():
    parser = argparse.ArgumentParser(description="Capturas del dashboard")
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--salida", default="docs/ux/antes")
    parser.add_argument(
        "--credenciales", default=str(RAIZ / "data" / "credenciales_prueba.md")
    )
    parser.add_argument("--solo-sin-datos", action="store_true")
    parser.add_argument(
        "--anchos",
        default="escritorio,laptop",
        help=f"Separados por coma, de {', '.join(ANCHOS)}",
    )
    parser.add_argument(
        "--sin-respuesta",
        action="store_true",
        help="No pregunta al asistente (no necesita Ollama)",
    )
    args = parser.parse_args()

    claves = credenciales(Path(args.credenciales))
    faltan = [u for u in ("admin", CLIENTE_DEMO) if u not in claves]
    if faltan:
        sys.exit(f"Faltan en {args.credenciales}: {', '.join(faltan)}")
    salida = Path(args.salida).resolve()
    salida.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        for ancho in args.anchos.split(","):
            print(f"{ancho} ({ANCHOS[ancho]} px)")
            page = navegador.new_page(locale="es-MX")
            if args.solo_sin_datos:
                sin_datos(page, args.url, salida, ancho, claves)
            else:
                recorrido(page, args.url, salida, ancho, claves, not args.sin_respuesta)
            page.close()
        navegador.close()


if __name__ == "__main__":
    main()

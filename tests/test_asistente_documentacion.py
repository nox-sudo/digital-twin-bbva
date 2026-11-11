"""
tests/test_asistente_documentacion.py

Busqueda en la documentacion (asistente/documentacion.py) y la herramienta
buscar_documentacion: fragmentos, terminos, ranking BM25, control por rol,
paso por el bucle de conversacion y una regresion contra el banco de
experimentos/eval_rag_documentacion.yaml.
"""

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))

from asistente import conversacion as c  # noqa: E402
from asistente import documentacion as d  # noqa: E402
from asistente import herramientas as h  # noqa: E402
from datos_asistente import contexto  # noqa: E402
from experimentos import eval_rag_documentacion as ev  # noqa: E402

DOC = """# Manual

Intro del manual.

## Seguridad

La sal del hash vive en `.env` y nunca se versiona.

```bash
# esto no es un encabezado
echo hola
```

## Datos

| Columna | Tipo |
|---|---|
| cliente_id | texto |
| probabilidad_impago | numero |

### Riesgos aceptados

Se acepto que el repositorio sea publico.
"""


# --- Fragmentos ---------------------------------------------------------------


def test_fragmentos_con_su_ruta_de_encabezados():
    fragmentos = d.fragmentar(DOC, "docs/manual.md")
    assert [f.seccion for f in fragmentos] == [
        "Manual",
        "Manual > Seguridad",
        "Manual > Datos",
        "Manual > Datos > Riesgos aceptados",
    ]
    assert fragmentos[1].fuente == "docs/manual.md § Manual > Seguridad"


def test_un_comentario_en_codigo_no_es_encabezado():
    seguridad = d.fragmentar(DOC, "m.md")[1]
    assert "# esto no es un encabezado" in seguridad.texto


def test_tabla_sin_fila_de_separadores():
    datos = d.fragmentar(DOC, "m.md")[2]
    assert "|---|" not in datos.texto
    assert "| probabilidad_impago | numero |" in datos.texto


def test_seccion_larga_se_parte_sin_cortar_parrafos(monkeypatch):
    monkeypatch.setattr(d, "PALABRAS_POR_FRAGMENTO", 10)
    parrafos = [" ".join(f"p{i}w{j}" for j in range(6)) for i in range(4)]
    texto = "## Larga\n\n" + "\n\n".join(parrafos)
    fragmentos = d.fragmentar(texto, "m.md")
    assert len(fragmentos) == 4
    assert [f.texto for f in fragmentos] == parrafos


# --- Terminos -----------------------------------------------------------------


def test_terminos_sin_acentos_ni_palabras_vacias():
    assert d.terminos("¿Qué es la protección?") == d.terminos("proteccion")


def test_identificadores_enteros_y_por_partes():
    t = d.terminos("probabilidad_impago")
    assert t[0] == "probabilidad_impago"
    assert set(d.terminos("probabilidad impago")) <= set(t)


def test_formas_verbales_comparten_raiz():
    assert d.terminos("aceptaron") == d.terminos("aceptados")
    assert d.terminos("respaldar") == d.terminos("respaldo")


# --- Ranking ------------------------------------------------------------------


def test_busca_por_encabezado_y_por_texto():
    indice = d.Indice(d.fragmentar(DOC, "m.md"))
    assert indice.buscar("¿Qué riesgos se aceptaron?")[0].fragmento.seccion.endswith(
        "Riesgos aceptados"
    )
    assert indice.buscar("¿dónde está la sal?")[0].fragmento.seccion.endswith(
        "Seguridad"
    )


def test_sin_terminos_en_comun_no_hay_resultados():
    indice = d.Indice(d.fragmentar(DOC, "m.md"))
    assert indice.buscar("receta de enchiladas") == []
    assert indice.buscar("¿qué?") == []


def test_respeta_k_y_ordena_por_puntaje():
    indice = d.Indice(d.fragmentar(DOC, "m.md"))
    resultados = indice.buscar("manual seguridad datos riesgos", k=2)
    assert len(resultados) == 2
    assert resultados[0].puntaje >= resultados[1].puntaje


def test_el_indice_del_repo_cubre_readme_y_docs():
    archivos = {f.archivo for f in d.indice_del_repo().fragmentos}
    assert "README.md" in archivos
    assert "docs/arquitectura.md" in archivos
    assert all(a == "README.md" or a.startswith("docs/") for a in archivos)


# --- Herramienta --------------------------------------------------------------


def _ctx(tmp_path, rol):
    return contexto(str(tmp_path / "gold.duckdb"), tmp_path, rol=rol)


def test_admin_recibe_fragmentos_con_fuente(tmp_path):
    r = h.ejecutar(
        "buscar_documentacion",
        {"consulta": "¿Qué riesgos de seguridad se aceptaron?"},
        _ctx(tmp_path, "admin"),
    )
    assert 1 <= len(r["fragmentos"]) <= h.FRAGMENTOS_DOCUMENTACION
    assert r["fragmentos"][0]["fuente"].startswith("docs/seguridad.md § ")
    assert "cita la fuente" in r["lectura"]
    json.dumps(r, allow_nan=False)


def test_cliente_rechazado_sin_tocar_el_indice(tmp_path, monkeypatch):
    def no_debe_llamarse():
        raise AssertionError("el indice no debe consultarse para un cliente")

    monkeypatch.setattr(d, "indice_del_repo", no_debe_llamarse)
    r = h.ejecutar(
        "buscar_documentacion", {"consulta": "seguridad"}, _ctx(tmp_path, "cliente")
    )
    assert r == {"error": h.HERRAMIENTAS["buscar_documentacion"]["error_rol"]}
    assert "documentacion" in r["error"] and "cifras" not in r["error"]
    assert "buscar_documentacion" not in json.dumps(h.esquemas("cliente"))


@pytest.mark.parametrize("consulta", ["", "   ", "x" * (h.LARGO_MAXIMO_CONSULTA + 1)])
def test_consulta_vacia_o_larga_es_error(tmp_path, consulta):
    r = h.ejecutar(
        "buscar_documentacion", {"consulta": consulta}, _ctx(tmp_path, "admin")
    )
    assert "error" in r


def test_sin_resultados(tmp_path):
    r = h.ejecutar(
        "buscar_documentacion",
        # Terminos que no aparecen en ningun documento (una pregunta real
        # citada en la documentacion dejaria de servir para esta prueba).
        {"consulta": "zqxw flurbo"},
        _ctx(tmp_path, "admin"),
    )
    assert r["sin_resultados"] is True


def test_prompt_nombra_la_herramienta_solo_para_admin(tmp_path):
    assert "buscar_documentacion" in c.prompt_sistema(
        _ctx(tmp_path, "admin"), "2027-02"
    )
    assert "buscar_documentacion" not in c.prompt_sistema(
        _ctx(tmp_path, "cliente"), "2027-02"
    )


class _ModeloFalso:
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.recibidos = []

    def invoke(self, mensajes):
        self.recibidos.append([dict(m) for m in mensajes])
        return self.respuestas.pop(0)


def test_los_fragmentos_llegan_al_modelo(tmp_path):
    modelo = _ModeloFalso(
        {
            "content": "",
            "tool_calls": [
                {
                    "name": "buscar_documentacion",
                    "args": {"consulta": "¿Por qué no usaron Great Expectations?"},
                    "id": "1",
                }
            ],
        },
        {"content": "Por lo que dice arquitectura.md...", "tool_calls": []},
    )
    r = c.responder(
        "¿Por qué no usaron Great Expectations?",
        [],
        _ctx(tmp_path, "admin"),
        modelo,
        "2027-02",
    )
    assert [p.herramienta for p in r.pasos] == ["buscar_documentacion"]
    enviado = json.loads(modelo.recibidos[1][-1]["content"])
    fuentes = [f["fuente"] for f in enviado["fragmentos"]]
    assert any("Decisiones de diseno" in f for f in fuentes)


# --- Regresion contra el banco ------------------------------------------------


def test_las_secciones_esperadas_del_banco_existen():
    """Si se renombra un encabezado, el banco debe actualizarse: si no,
    mediria contra una seccion que ya no existe."""
    fragmentos = d.indice_del_repo().fragmentos
    for p in ev.cargar_banco()["preguntas"]:
        for e in p["esperado"]:
            assert any(
                f.archivo == e["archivo"] and e["seccion"] in f.seccion
                for f in fragmentos
            ), (p["id"], e)


def test_recuperacion_no_retrocede():
    """17 de 20 preguntas directas al 2026-11-11; el piso deja margen para
    una edicion de la documentacion, no para un cambio que rompa la busqueda."""
    r = ev.evaluar(d.indice_del_repo(), ev.cargar_banco())
    aciertos, total = r["directas"]
    assert aciertos / total >= 0.8, r["filas"]

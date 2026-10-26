"""
tests/test_dag_contrato.py

Contrato entre el DAG de Airflow y los scripts que ejecuta.

El DAG (dags/gemelo_pipeline_dag.py) solo se importa dentro del contenedor
de Airflow, asi que ni las pruebas ni el CI lo ejecutan. Si un script
cambia el nombre de un argumento (por ejemplo --auditoria-out), el smoke
test de CI se actualiza junto con el script, pero el DAG sigue mandando el
nombre viejo y el error aparece hasta la siguiente corrida en Docker.

Estas pruebas leen el DAG como texto con ast, sin importar Airflow, y
verifican que:

1. cada tarea llama a un script que existe en la raiz del proyecto;
2. cada --opcion que el DAG le pasa a un script aparece en el --help de
   ese script (es decir, argparse la acepta);
3. los task_id son unicos y la cadena de dependencias recorre todas las
   tareas una sola vez, en el orden del pipeline.
"""

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
DAG = RAIZ / "dags" / "gemelo_pipeline_dag.py"

ORDEN_ESPERADO = [
    "generar_fuentes_sinteticas",
    "publicar_landing",
    "ingesta_bronze",
    "transformacion_silver",
    "transformacion_gold",
    "construir_features",
    "generar_etiquetas",
    "entrenar_modelo",
    "predict_risk",
    "monitorear_deriva",
]


def _tareas_del_dag() -> dict[str, dict]:
    """Devuelve {variable: {"task_id", "command"}} de cada tarea_worker(...).

    Python une las cadenas literales adyacentes al compilar, asi que un
    comando partido en varias lineas llega como una sola constante.
    """
    arbol = ast.parse(DAG.read_text(encoding="utf-8"))
    tareas = {}
    for nodo in ast.walk(arbol):
        if not (isinstance(nodo, ast.Assign) and isinstance(nodo.value, ast.Call)):
            continue
        llamada = nodo.value
        if getattr(llamada.func, "id", None) != "tarea_worker":
            continue
        task_id, command = (ast.literal_eval(arg) for arg in llamada.args)
        tareas[nodo.targets[0].id] = {"task_id": task_id, "command": command}
    return tareas


def _cadena_de_dependencias() -> list[str]:
    """Aplana la expresion a >> b >> c en [a, b, c] (nombres de variable)."""
    arbol = ast.parse(DAG.read_text(encoding="utf-8"))

    def aplanar(nodo):
        if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.RShift):
            return aplanar(nodo.left) + aplanar(nodo.right)
        if isinstance(nodo, ast.Name):
            return [nodo.id]
        raise AssertionError(f"Nodo inesperado en la cadena del DAG: {ast.dump(nodo)}")

    cadenas = [
        aplanar(n.value)
        for n in ast.walk(arbol)
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.BinOp)
        and isinstance(n.value.op, ast.RShift)
    ]
    assert (
        len(cadenas) == 1
    ), f"Se esperaba una sola cadena >> en el DAG, hay {len(cadenas)}"
    return cadenas[0]


def _script_y_opciones(command: str) -> tuple[str, list[str]]:
    # Las plantillas de Jinja ({{ ... }}) son valores que Airflow resuelve al
    # correr; se quitan para no confundir sus llaves con opciones.
    sin_plantillas = re.sub(r"\{\{.*?\}\}", "", command)
    partes = sin_plantillas.split()
    opciones = [p for p in partes[1:] if p.startswith("--")]
    return partes[0], opciones


TAREAS = _tareas_del_dag()


def test_el_dag_define_las_tareas_esperadas():
    assert sorted(t["task_id"] for t in TAREAS.values()) == sorted(ORDEN_ESPERADO)


def test_task_ids_unicos():
    ids = [t["task_id"] for t in TAREAS.values()]
    assert len(ids) == len(set(ids))


def test_cadena_recorre_todas_las_tareas_en_orden():
    cadena = _cadena_de_dependencias()
    assert len(cadena) == len(set(cadena)), "Una tarea aparece dos veces en la cadena"
    assert [TAREAS[v]["task_id"] for v in cadena] == ORDEN_ESPERADO


@pytest.mark.parametrize(
    "task_id,command",
    [(t["task_id"], t["command"]) for t in TAREAS.values()],
    ids=[t["task_id"] for t in TAREAS.values()],
)
def test_script_acepta_las_opciones_del_dag(task_id, command):
    script, opciones = _script_y_opciones(command)
    ruta = RAIZ / script
    assert ruta.is_file(), f"{task_id}: {script} no existe en la raiz del proyecto"

    # --help ejecuta el mismo argparse que usara el worker, sin correr el paso.
    resultado = subprocess.run(
        [sys.executable, str(ruta), "--help"],
        capture_output=True,
        text=True,
        cwd=RAIZ,
        timeout=120,
    )
    assert resultado.returncode == 0, f"{script} --help fallo:\n{resultado.stderr}"

    aceptadas = set(re.findall(r"--[a-z0-9][a-z0-9-]*", resultado.stdout))
    faltantes = [o for o in opciones if o not in aceptadas]
    assert not faltantes, f"{task_id}: {script} no acepta {faltantes}"

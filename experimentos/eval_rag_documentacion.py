"""
experimentos/eval_rag_documentacion.py

Mide la recuperacion de asistente/documentacion.py contra el banco
experimentos/eval_rag_documentacion.yaml: proporcion de preguntas cuya
seccion esperada sale entre los primeros k fragmentos (acierto@k), aparte
para las preguntas directas y las de parafrasis. No usa el modelo: mide solo
lo que el modelo recibiria.

Uso:
    uv run python experimentos/eval_rag_documentacion.py [--k 3] [--detalle]
"""

import argparse
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from asistente.documentacion import Indice, indice_del_repo  # noqa: E402

BANCO = RAIZ / "experimentos" / "eval_rag_documentacion.yaml"


def cargar_banco(ruta: Path = BANCO) -> dict:
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def acierta(resultados, esperado: list[dict]) -> int | None:
    """Posicion (1..k) del primer fragmento de una seccion esperada, o None."""
    for posicion, r in enumerate(resultados, start=1):
        for e in esperado:
            if (
                r.fragmento.archivo == e["archivo"]
                and e["seccion"] in r.fragmento.seccion
            ):
                return posicion
    return None


def evaluar(indice: Indice, banco: dict, k: int | None = None) -> dict:
    k = k or int(banco["k"])
    filas = []
    for p in banco["preguntas"]:
        resultados = indice.buscar(p["pregunta"], k=k)
        filas.append(
            {
                "id": p["id"],
                "pregunta": p["pregunta"],
                "parafrasis": bool(p.get("parafrasis")),
                "posicion": acierta(resultados, p["esperado"]),
                "fuentes": [r.fragmento.fuente for r in resultados],
            }
        )
    directas = [f for f in filas if not f["parafrasis"]]
    parafrasis = [f for f in filas if f["parafrasis"]]
    fuera = {
        q: [r.fragmento.fuente for r in indice.buscar(q, k=k)]
        for q in banco.get("fuera_del_corpus", [])
    }

    def tasa(grupo):
        return sum(f["posicion"] is not None for f in grupo), len(grupo)

    return {
        "k": k,
        "filas": filas,
        "directas": tasa(directas),
        "parafrasis": tasa(parafrasis),
        "primer_lugar": sum(f["posicion"] == 1 for f in filas),
        "fuera_del_corpus": fuera,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--k", type=int)
    parser.add_argument("--detalle", action="store_true")
    args = parser.parse_args()

    r = evaluar(indice_del_repo(), cargar_banco(), args.k)
    print(f"Fragmentos en el indice: {len(indice_del_repo().fragmentos)}")
    print(f"Acierto@{r['k']}, directas:   {r['directas'][0]} de {r['directas'][1]}")
    print(f"Acierto@{r['k']}, parafrasis: {r['parafrasis'][0]} de {r['parafrasis'][1]}")
    print(f"En primer lugar: {r['primer_lugar']} de {len(r['filas'])}")
    for f in r["filas"]:
        if args.detalle or f["posicion"] is None:
            marca = f["posicion"] or "-"
            print(f"  [{marca}] {f['id']} {f['pregunta']}")
            for fuente in f["fuentes"]:
                print(f"        {fuente}")
    print("Fuera del corpus (fragmentos devueltos):")
    for q, fuentes in r["fuera_del_corpus"].items():
        print(f"  {q} -> {len(fuentes)}")


if __name__ == "__main__":
    main()

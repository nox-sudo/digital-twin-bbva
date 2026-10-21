"""
experimentos/sondeo_herramientas.py

Sondeo de las herramientas del asistente (asistente/herramientas.py) con un
modelo local de Ollama: para un conjunto fijo de preguntas en espanol, que
herramienta elige el modelo, con que argumentos, y si la llamada se puede
ejecutar. Sirve para ver donde se equivoca el modelo antes de construir la
conversacion encima, y para comparar despues de cambiar descripciones o
reglas de normalizacion.

Reproducibilidad: temperature 0 y semilla fija, y cada pregunta se repite
--repeticiones veces. Si las corridas de una pregunta no coinciden, el
resultado de esa pregunta no es estable y no se debe leer como el
comportamiento del modelo.

Solo lee Gold y llama a Ollama en localhost; no escribe nada. No corre en CI
(necesita Ollama con el modelo descargado). Uso:

    uv run python experimentos/sondeo_herramientas.py
    uv run python experimentos/sondeo_herramientas.py --cliente CLI-000162 \\
        --repeticiones 5
"""

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.getcwd())

from asistente.herramientas import Contexto, ejecutar, esquemas  # noqa: E402
from dashboard import datos  # noqa: E402

PREGUNTAS = [
    "¿Cuánto gasté en comida en marzo?",
    "¿Cuál es mi riesgo de impago?",
    "¿Podría pagar una casa de 1.2 millones a 15 años?",
    "¿Cómo estoy comparado con los demás clientes en capacidad de ahorro?",
    "¿Cómo va el portafolio contra la base?",
    "¿En qué se me va el dinero?",
    "¿Cuál fue mi flujo en los últimos 3 meses?",
    "¿Qué acciones me recomiendas comprar?",
]


def prompt_sistema(ultimo_mes: str) -> str:
    return (
        "Eres el asistente financiero del Gemelo Digital. Respondes en espanol "
        "sobre el cliente seleccionado y el portafolio. Para cualquier cifra usa "
        "una herramienta; nunca inventes numeros. El ultimo mes con datos es "
        f"{ultimo_mes}."
    )


def preguntar(url: str, modelo: str, sistema: str, pregunta: str, semilla: int):
    cuerpo = {
        "model": modelo,
        "stream": False,
        "options": {"temperature": 0, "seed": semilla},
        "messages": [
            {"role": "system", "content": sistema},
            {"role": "user", "content": pregunta},
        ],
        "tools": esquemas(),
    }
    peticion = urllib.request.Request(
        f"{url}/api/chat",
        json.dumps(cuerpo).encode(),
        {"Content-Type": "application/json"},
    )
    inicio = time.perf_counter()
    with urllib.request.urlopen(peticion, timeout=300) as respuesta:
        mensaje = json.load(respuesta)["message"]
    llamadas = [
        (c["function"]["name"], c["function"]["arguments"])
        for c in mensaje.get("tool_calls") or []
    ]
    return llamadas, mensaje.get("content", ""), time.perf_counter() - inicio


def estado_de(resultado: dict) -> str:
    if "error" in resultado:
        return "error: " + resultado["error"][:90]
    if resultado.get("sin_movimientos"):
        return "ok, sin movimientos"
    return "ok"


def main():
    parser = argparse.ArgumentParser(
        description="Que herramienta elige el modelo para cada pregunta"
    )
    parser.add_argument("--modelo", default="llama3.1:8b")
    parser.add_argument("--cliente", default="CLI-000283")
    parser.add_argument("--repeticiones", type=int, default=3)
    parser.add_argument("--semilla", type=int, default=42)
    parser.add_argument("--url", default="http://localhost:11434")
    args = parser.parse_args()

    ctx = Contexto.desde_config(args.cliente)
    ultimo_mes = datos.flujo_mensual(ctx.ruta_gold, args.cliente)["mes"].max()
    sistema = prompt_sistema(ultimo_mes)
    print(
        f"Modelo {args.modelo}, cliente {args.cliente}, ultimo mes {ultimo_mes}, "
        f"temperature 0, semilla {args.semilla}, {args.repeticiones} corridas "
        "por pregunta\n"
    )

    estables = 0
    for pregunta in PREGUNTAS:
        corridas = [
            preguntar(args.url, args.modelo, sistema, pregunta, args.semilla)
            for _ in range(args.repeticiones)
        ]
        herramientas = [tuple(n for n, _ in llamadas) for llamadas, _, _ in corridas]
        firmas = [json.dumps(llamadas, sort_keys=True) for llamadas, _, _ in corridas]
        misma_herramienta = len(set(herramientas)) == 1
        mismos_argumentos = len(set(firmas)) == 1
        estables += mismos_argumentos
        print(pregunta)
        for i, (llamadas, texto, segundos) in enumerate(corridas, 1):
            if not llamadas:
                print(
                    f"  corrida {i} ({segundos:.1f} s): sin herramienta, {texto[:90]!r}"
                )
                continue
            for nombre, argumentos in llamadas:
                print(
                    f"  corrida {i} ({segundos:.1f} s): {nombre} {argumentos} -> "
                    f"{estado_de(ejecutar(nombre, argumentos, ctx))}"
                )
        print(
            f"  coinciden las {args.repeticiones}: herramienta "
            f"{'si' if misma_herramienta else 'NO'}, argumentos "
            f"{'si' if mismos_argumentos else 'NO'}\n"
        )
    print(
        f"RESULT {estables} de {len(PREGUNTAS)} preguntas con las "
        f"{args.repeticiones} corridas identicas (herramienta y argumentos)"
    )


if __name__ == "__main__":
    main()

"""
simulate_mortgage.py

Simula el escenario "contratar una hipoteca" para un cliente con el
modelo Monte Carlo de src/simulacion/montecarlo.py y reporta:

  - cuota mensual y su proporcion del ingreso del cliente;
  - si alcanza el enganche y, si no, en cuantos meses lo juntaria;
  - probabilidad de quedar en saldo negativo en el horizonte, con y sin
    la hipoteca (sobre los mismos meses y choques simulados);
  - capacidad hipotecaria: precio maximo de vivienda con el que la
    hipoteca no agrega mas riesgo del que permite config/simulacion.yaml.

Solo lee Gold (DuckDB); no escribe nada. No necesita Spark.

Uso:
    python simulate_mortgage.py --cliente CLI-000001 --precio 1500000
    python main.py simulate --cliente CLI-000001 --precio 1500000 --plazo 15
"""

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.formato import formato_mxn  # noqa: E402
from src.common.mercado import precio_minimo_vivienda  # noqa: E402
from src.simulacion.montecarlo import (  # noqa: E402
    Hipoteca,
    Supuestos,
    capacidad_maxima,
    evaluar_hipoteca,
    historial_cliente,
    meses_para_juntar,
    muestrear,
    resumir,
    saldo_inicial_cliente,
    simular,
)


def cargar_config(ruta: str) -> dict:
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def supuestos_desde_config(cfg: dict, semilla: int | None = None) -> Supuestos:
    sim = cfg["simulacion"]
    choque = cfg["choque_ingreso"]
    return Supuestos(
        meses=int(sim["meses"]),
        trayectorias=int(sim["trayectorias"]),
        semilla=int(semilla if semilla is not None else sim["semilla"]),
        prob_choque_mensual=float(choque["probabilidad_mensual"]),
        duracion_choque=tuple(int(x) for x in choque["duracion_meses"]),
    )


def pesos(valor: float) -> str:
    return formato_mxn(valor)


def main():
    parser = argparse.ArgumentParser(
        description="Simulacion Monte Carlo de una hipoteca"
    )
    parser.add_argument("--cliente", required=True)
    parser.add_argument(
        "--precio", type=float, required=True, help="Precio de la vivienda"
    )
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument("--config", default="config/simulacion.yaml")
    parser.add_argument("--tasa", type=float, help="Tasa anual, por ejemplo 0.11")
    parser.add_argument("--plazo", type=int, help="Plazo en anios")
    parser.add_argument(
        "--enganche", type=float, help="Fraccion del precio, por ejemplo 0.10"
    )
    parser.add_argument(
        "--renta",
        type=float,
        default=0.0,
        help="Renta mensual que la vivienda reemplaza",
    )
    parser.add_argument("--semilla", type=int)
    args = parser.parse_args()

    cfg = cargar_config(args.config)
    condiciones = cfg["hipoteca"]
    hipoteca = Hipoteca(
        precio=args.precio,
        tasa_anual=args.tasa if args.tasa is not None else condiciones["tasa_anual"],
        plazo_anios=(
            args.plazo if args.plazo is not None else condiciones["plazo_anios"]
        ),
        enganche_pct=(
            args.enganche if args.enganche is not None else condiciones["enganche_pct"]
        ),
        gastos_iniciales_pct=condiciones["gastos_iniciales_pct"],
        renta_reemplazada=args.renta,
    )
    supuestos = supuestos_desde_config(cfg, args.semilla)
    incremento = float(cfg["incremento_deficit_maximo"])

    historial = historial_cliente(args.gold, args.cliente)
    saldo = saldo_inicial_cliente(args.gold, args.cliente)
    muestras = muestrear(len(historial.meses), supuestos)

    sin_hipoteca = resumir(simular(historial, saldo, muestras))
    con_hipoteca = evaluar_hipoteca(historial, saldo, hipoteca, muestras)
    capacidad = capacidad_maxima(
        historial,
        saldo,
        hipoteca,
        muestras,
        incremento,
        precio_minimo=precio_minimo_vivienda(),
    )
    meses_enganche = meses_para_juntar(hipoteca.pago_inicial, historial, saldo)

    print(f"\n=== {args.cliente}: hipoteca de {pesos(hipoteca.precio)} ===")
    print(
        f"Ingreso laboral medio: {pesos(historial.ingreso_medio)} | "
        f"saldo disponible: {pesos(saldo)} | historial: {len(historial.meses)} meses"
    )
    print(
        f"Credito: {pesos(hipoteca.monto_credito)} a {hipoteca.plazo_anios} anios, "
        f"tasa {hipoteca.tasa_anual:.1%} | pago inicial {pesos(hipoteca.pago_inicial)}"
    )
    if meses_enganche == 0:
        print("Enganche y gastos: los cubre con su saldo actual")
    elif meses_enganche is None:
        print("Enganche y gastos: con su flujo actual no ahorra, no los reuniria")
    else:
        print(
            f"Enganche y gastos: a su ritmo de ahorro los reuniria en "
            f"{meses_enganche:.0f} meses ({meses_enganche / 12:.1f} anios)"
        )
    cuota_ingreso = (
        hipoteca.cuota_mensual / historial.ingreso_medio
        if historial.ingreso_medio
        else float("inf")
    )
    print(
        f"Cuota mensual: {pesos(hipoteca.cuota_mensual)} "
        f"({cuota_ingreso:.0%} del ingreso laboral medio)"
        + (f"; reemplaza una renta de {pesos(args.renta)}" if args.renta else "")
    )
    print(
        f"\nProbabilidad de saldo negativo en {supuestos.meses} meses "
        f"({supuestos.trayectorias:,} trayectorias):"
    )
    print(f"  sin hipoteca: {sin_hipoteca.probabilidad_deficit:.1%}")
    print(f"  con hipoteca: {con_hipoteca.probabilidad_deficit:.1%}")
    if con_hipoteca.mes_mediano_primer_deficit is not None:
        print(
            "  cuando ocurre, el primer mes negativo es, en la mediana, el "
            f"{con_hipoteca.mes_mediano_primer_deficit:.0f}"
        )
    print(
        f"Saldo al mes {supuestos.meses} (mediana) con hipoteca: "
        f"{pesos(con_hipoteca.saldo_final_p50)}"
    )
    print(
        f"\nCapacidad hipotecaria (la hipoteca agrega a lo mas {incremento:.0%} "
        f"de riesgo de deficit): "
        + (
            f"vivienda de hasta {pesos(capacidad.monto)}"
            if capacidad.aplica
            else f"no aplica. {capacidad.detalle}"
        )
    )


if __name__ == "__main__":
    main()

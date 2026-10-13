"""
experimentos/limites_etiquetas.py

Prueba los limites de la etiqueta de impago como evento posterior
(src/gold/etiquetas_impago.py) y de su evaluacion. No cambia ningun parametro de
config/etiquetas_impago.yaml: las configuraciones extremas se arman en memoria
con dataclasses.replace, solo para ver como se comporta el modulo. Son pruebas de
sensibilidad, no iteraciones del diseno.

Dos partes:

  bordes  Entradas degeneradas (0, 1, 2 y 10 clientes, columnas constantes, NaN,
          infinitos, ids repetidos, un valor extremo) y configuraciones extremas
          (tasa objetivo casi 0 o casi 1, coeficientes enormes, sin choques,
          horizonte de 1 y de 120 meses). Para cada caso imprime que paso:
          resultado o error, y con que mensaje. Mide tambien tiempo y memoria
          con 50,000 y 500,000 clientes. No necesita XGBoost.

  tamano  AUC contra el tamano de la muestra. Toma las features y las etiquetas
          de un Gold ya construido, saca submuestras aleatorias de tamano n y, en
          cada una, mide el AUC fuera de pliegue (validacion cruzada
          estratificada de 5 pliegues) del mismo XGBoost de train_model.py, junto
          con el del oraculo. Responde a partir de cuantos clientes el AUC deja de
          depender del azar de la muestra. Necesita XGBoost (en macOS, libomp).

Uso:

    uv run python experimentos/limites_etiquetas.py --parte bordes
    uv run python experimentos/limites_etiquetas.py --parte tamano \
        --gold data/gold/kpis.duckdb \
        --auditoria data/auditoria/etiquetas_latentes.parquet
"""

import argparse
import dataclasses
import datetime as dt
import os
import resource
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.getcwd())

from src.gold.etiquetas_impago import (  # noqa: E402
    cargar_config,
    generar_etiquetas,
)

FECHA = dt.date(2026, 9, 30)
SEMILLA = 42


def features_sinteticas(n: int, semilla: int = 7) -> pd.DataFrame:
    """Mismas formas que las features reales: 33 % sin prestamo, 46 % sin tarjeta."""
    rng = np.random.default_rng(semilla)
    ratio = np.where(rng.random(n) < 0.33, 0.0, rng.lognormal(0.0, 0.9, n))
    uso = np.where(rng.random(n) < 0.46, 0.0, rng.beta(2.0, 4.0, n) * 0.6)
    ahorro = rng.normal(7000.0, 4300.0, n)
    return pd.DataFrame(
        {
            "ratio_endeudamiento": ratio,
            "uso_linea_credito": uso,
            "capacidad_ahorro": ahorro,
        },
        index=pd.Index([f"C{i:07d}" for i in range(n)], name="cliente_id"),
    )


def intentar(nombre: str, features: pd.DataFrame, cfg) -> None:
    """Corre generar_etiquetas y cuenta que paso, sin ocultar el tipo de error."""
    t0 = time.perf_counter()
    try:
        tabla, _, resultado = generar_etiquetas(features, cfg, FECHA)
    except Exception as error:  # noqa: BLE001 - el objetivo es ver cualquier fallo
        texto = str(error).splitlines()[0][:110] if str(error) else ""
        print(f"RESULT bordes {nombre}: ERROR {type(error).__name__}: {texto}")
        return
    segundos = time.perf_counter() - t0
    positivos = int(tabla["impago_posterior"].sum())
    p_media = float(resultado.proceso["p"].mean())
    print(
        f"RESULT bordes {nombre}: OK n={len(tabla)} positivos={positivos} "
        f"tasa_realizada={resultado.tasa_realizada:.4f} p_media={p_media:.6f} "
        f"(objetivo {cfg.tasa_objetivo}) b0={resultado.b0:.3f} "
        f"segundos={segundos:.2f}"
    )


def parte_bordes(cfg) -> None:
    base = features_sinteticas(500)
    cambia = dataclasses.replace

    print("RESULT bordes --- entradas degeneradas (configuracion del repo) ---")
    intentar("0 clientes", base.iloc[0:0], cfg)
    intentar("1 cliente", base.iloc[:1], cfg)
    intentar("2 clientes", base.iloc[:2], cfg)
    intentar("10 clientes", base.iloc[:10], cfg)
    constantes = base.copy()
    for columna in constantes.columns:
        constantes[columna] = 5.0
    intentar("columnas constantes", constantes, cfg)
    sin_producto = base.copy()
    sin_producto[["ratio_endeudamiento", "uso_linea_credito"]] = 0.0
    intentar("nadie tiene producto", sin_producto, cfg)
    con_nan = base.copy()
    con_nan.iloc[3, 2] = np.nan
    intentar("NaN en capacidad_ahorro", con_nan, cfg)
    con_nan_producto = base.copy()
    con_nan_producto.iloc[3, 0] = np.nan
    intentar("NaN en ratio_endeudamiento", con_nan_producto, cfg)
    con_inf = base.copy()
    con_inf.iloc[3, 2] = np.inf
    intentar("infinito en capacidad_ahorro", con_inf, cfg)
    repetidos = pd.concat([base, base.iloc[:1]])
    muchos_inf = base.copy()
    muchos_inf.iloc[:25, 2] = np.inf
    intentar("5 % de infinitos en capacidad_ahorro", muchos_inf, cfg)
    intentar("cliente_id repetido", repetidos, cfg)
    extremo = base.copy()
    extremo.iloc[0, 0] = 1e12
    intentar("un ratio de 1e12", extremo, cfg)
    sin_columna = base.drop(columns=["capacidad_ahorro"])
    intentar("falta una columna", sin_columna, cfg)

    print("RESULT bordes --- configuraciones extremas (500 clientes) ---")
    for tasa in (1e-6, 0.001, 0.05, 0.5, 0.95, 0.999999):
        intentar(f"tasa_objetivo={tasa}", base, cambia(cfg, tasa_objetivo=tasa))
    intentar(
        "coeficientes x50",
        base,
        cambia(
            cfg,
            b_ratio_endeudamiento=40.0,
            b_uso_linea_credito=25.0,
            b_capacidad_ahorro=35.0,
            b_rasgo_oculto=40.0,
            b_choque=50.0,
        ),
    )
    intentar(
        "todos los coeficientes en 0",
        base,
        cambia(
            cfg,
            b_ratio_endeudamiento=0.0,
            b_uso_linea_credito=0.0,
            b_capacidad_ahorro=0.0,
            b_rasgo_oculto=0.0,
            b_choque=0.0,
        ),
    )
    intentar("coeficiente NaN", base, cambia(cfg, b_choque=float("nan")))
    intentar("coeficiente infinito", base, cambia(cfg, b_choque=float("inf")))
    intentar("sin choques (q=0)", base, cambia(cfg, q_choque_mensual=0.0))
    intentar("choques casi seguros (q=0.99)", base, cambia(cfg, q_choque_mensual=0.99))
    intentar("horizonte de 1 mes", base, cambia(cfg, horizonte_meses=1))
    intentar("horizonte de 120 meses", base, cambia(cfg, horizonte_meses=120))
    intentar(
        "sin winsorizar (0 y 1)",
        base,
        cambia(cfg, winsor_inferior=0.0, winsor_superior=1.0),
    )
    intentar(
        "winsor muy cerrado (0.49 y 0.51)",
        base,
        cambia(cfg, winsor_inferior=0.49, winsor_superior=0.51),
    )
    intentar("semilla 0", base, cambia(cfg, semilla=0))
    intentar("semilla 2**63", base, cambia(cfg, semilla=2**63))
    intentar("semilla negativa", base, cambia(cfg, semilla=-1))

    print("RESULT bordes --- escala (configuracion del repo) ---")
    for n in (5_000, 50_000, 500_000):
        grande = features_sinteticas(n)
        antes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        intentar(f"{n} clientes", grande, cfg)
        despues = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # ru_maxrss: bytes en macOS, kilobytes en Linux.
        divisor = 1024**2 if sys.platform == "darwin" else 1024
        print(
            f"RESULT bordes memoria tras {n} clientes: pico del proceso "
            f"{despues / divisor:.0f} MB (antes {antes / divisor:.0f} MB)"
        )


def parte_tamano(args, cfg) -> None:
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold
    from xgboost import XGBClassifier

    from src.gold.etiquetas_gold import cargar_etiquetas
    from src.gold.risk_features import cargar_features, preparar_para_modelo

    features = cargar_features(args.gold)
    etiquetas = cargar_etiquetas(args.gold).set_index("cliente_id")
    auditoria = pd.read_parquet(args.auditoria).set_index("cliente_id")
    x = preparar_para_modelo(features)
    y = etiquetas["impago_posterior"].reindex(x.index).astype(int)
    p = auditoria["p"].reindex(x.index)
    print(
        f"RESULT tamano base: {len(y)} clientes, {int(y.sum())} positivos "
        f"({y.mean():.3f}), pliegues {args.pliegues}, submuestras {args.submuestras}"
    )

    def auc_fuera_de_pliegue(xs: pd.DataFrame, ys: pd.Series, semilla: int) -> float:
        fuera = np.zeros(len(xs))
        esquema = StratifiedKFold(args.pliegues, shuffle=True, random_state=semilla)
        for entrenamiento, prueba in esquema.split(xs, ys):
            positivos = int(ys.iloc[entrenamiento].sum())
            negativos = len(entrenamiento) - positivos
            modelo = XGBClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.05,
                scale_pos_weight=negativos / positivos,
                eval_metric="logloss",
                random_state=SEMILLA,
            ).fit(xs.iloc[entrenamiento], ys.iloc[entrenamiento])
            fuera[prueba] = modelo.predict_proba(xs.iloc[prueba])[:, 1]
        return roc_auc_score(ys, fuera)

    generador = np.random.default_rng(SEMILLA)
    for n in args.tamanos:
        if n > len(y):
            print(f"RESULT tamano n={n}: se omite, el Gold tiene {len(y)} clientes")
            continue
        repeticiones = 1 if n == len(y) else args.submuestras
        aucs, oraculos, positivos, no_evaluables = [], [], [], 0
        for r in range(repeticiones):
            if n == len(y):
                posiciones = np.arange(n)
            else:
                posiciones = np.sort(generador.choice(len(y), size=n, replace=False))
            ys, xs = y.iloc[posiciones], x.iloc[posiciones]
            k = int(ys.sum())
            if k < args.pliegues or (n - k) < args.pliegues:
                no_evaluables += 1
                continue
            positivos.append(k)
            aucs.append(auc_fuera_de_pliegue(xs, ys, SEMILLA + r))
            oraculos.append(roc_auc_score(ys, p.iloc[posiciones]))
        if not aucs:
            print(
                f"RESULT tamano n={n}: ninguna de {repeticiones} submuestras es "
                f"evaluable (menos de {args.pliegues} positivos o negativos)"
            )
            continue
        a, o = np.array(aucs), np.array(oraculos)
        print(
            f"RESULT tamano n={n}: submuestras evaluables {len(a)}/{repeticiones}, "
            f"positivos {min(positivos)}-{max(positivos)} | AUC modelo media "
            f"{a.mean():.3f} sd {a.std(ddof=1) if len(a) > 1 else float('nan'):.3f} "
            f"(min {a.min():.3f}, max {a.max():.3f}) | AUC oraculo media {o.mean():.3f} "
            f"| hueco medio {np.mean(o - a):.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Limites de la etiqueta de impago")
    parser.add_argument("--parte", choices=["bordes", "tamano"], required=True)
    parser.add_argument("--config", default="config/etiquetas_impago.yaml")
    parser.add_argument("--gold", default="data/gold/kpis.duckdb")
    parser.add_argument(
        "--auditoria", default="data/auditoria/etiquetas_latentes.parquet"
    )
    parser.add_argument("--pliegues", type=int, default=5)
    parser.add_argument("--submuestras", type=int, default=20)
    parser.add_argument(
        "--tamanos",
        type=int,
        nargs="+",
        default=[50, 100, 200, 500, 1000, 2500, 5000],
    )
    args = parser.parse_args()

    cfg = cargar_config(args.config)
    if args.parte == "bordes":
        parte_bordes(cfg)
    else:
        parte_tamano(args, cfg)


if __name__ == "__main__":
    main()

"""
src/gold/escalera.py

Escalera financiera de un cliente: en que paso esta y que le falta para el
siguiente, con los umbrales de config/escalera_financiera.yaml. Lo calcula
Python; el asistente y la vista cliente solo lo explican.

Pasos, en el orden de la configuracion (el actual es el primero que no
cumple):

  pagos_deuda   pago mensual comprometido de deuda <= 30 % del ingreso neto
                de referencia (CONDUSEF)
  uso_tarjeta   saldo de tarjetas <= 30 % del limite (regla del proyecto)
  fondo_minimo  saldo liquido >= 3 meses de gastos (CONDUSEF)
  ahorro        ahorro mensual >= 10 % del ingreso (regla del proyecto)
  fondo_meta    saldo liquido >= 6 meses de gastos (CONDUSEF)
  invertir      todo lo anterior cubierto

Pago mensual de deuda (opcion C, aprobada el 2026-10-22): el comprometido es
la medida y el pagado es la senal.
  - Comprometido: cuota fija del prestamo (monto original, plazo, tasa con IVA
    sobre intereses) mas el pago minimo de la tarjeta segun Banxico (el mayor
    entre 1.5 % del saldo mas intereses con IVA, y 1.25 % del limite).
  - Pagado: pagos de deuda observados en gold_movimientos_mensuales. Solo se
    usa para decir si en los ultimos meses dejo de pagar; no mueve el paso.
ratio_endeudamiento (saldo entre ingreso) no se usa aqui: es otra medida.

Ingreso de referencia: el promedio mensual de la ventana, salvo para ingreso
variable (la mayor parte llega como depositos), que usa el mes con menor
ingreso de la ventana, como pide CONDUSEF. Un mes sin ingreso no cuenta como
"el mes con menor ingreso": ese es el caso del fondo de emergencia.

Lee Gold (DuckDB, solo lectura): gold_features_cliente,
gold_movimientos_mensuales y gold_creditos_cliente. Sin Spark.
"""

from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from src.common.formato import formato_mxn

RAIZ = Path(__file__).resolve().parents[2]
RUTA_CONFIG = RAIZ / "config" / "escalera_financiera.yaml"

TIPOS_INGRESO = ("nomina", "deposito")
TIPOS_SALIDA = ("compra", "pago_recurrente", "retiro")

NOMBRES = {
    "pagos_deuda": "Pagos de deuda bajo control",
    "uso_tarjeta": "Tarjeta bajo control",
    "fondo_minimo": "Fondo de emergencia mínimo",
    "ahorro": "Ahorro constante",
    "fondo_meta": "Fondo de emergencia completo",
    "invertir": "Listo para invertir",
}


def cargar_config(ruta: Path = RUTA_CONFIG) -> dict:
    with open(ruta, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    desconocidos = set(cfg["orden"]) - set(NOMBRES)
    if desconocidos or cfg["orden"][-1] != "invertir":
        raise ValueError(
            f"{ruta}: orden con pasos desconocidos {sorted(desconocidos)} o sin "
            "'invertir' al final"
        )
    return cfg


@dataclass
class Paso:
    paso: str
    nombre: str
    cumple: bool
    valor: float | None
    umbral: float | None
    aplica: bool = True


@dataclass
class Escalera:
    cliente_id: str
    paso_actual: str
    numero_paso: int
    pasos: list[Paso]
    metricas: dict
    cumplimiento: dict
    lectura: str
    supuestos: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pago comprometido
# ---------------------------------------------------------------------------


def cuota_prestamo(
    monto: float, plazo_meses: int, tasa_anual: float, iva: float
) -> float:
    """Cuota fija (amortizacion francesa) con IVA sobre los intereses."""
    if not monto or monto <= 0 or not plazo_meses or plazo_meses <= 0:
        return 0.0
    i = tasa_anual * (1 + iva) / 12
    if i == 0:
        return monto / plazo_meses
    return monto * i / (1 - (1 + i) ** -plazo_meses)


def pago_minimo_tarjeta(
    saldo: float, limite: float, tasa_anual: float, iva: float, cfg_minimo: dict
) -> float:
    """Pago minimo segun Banxico (Circular 13/2011, numeral 2.1): el mayor
    entre 1.5 % del saldo mas los intereses con IVA, y 1.25 % del limite; si
    supera el saldo, el saldo."""
    if not saldo or saldo <= 0:
        return 0.0
    intereses = saldo * tasa_anual / 12 * (1 + iva)
    minimo = max(
        cfg_minimo["saldo_pct"] * saldo + intereses,
        cfg_minimo["limite_pct"] * (limite or 0.0),
    )
    return min(minimo, saldo)


def pago_comprometido(creditos: pd.DataFrame, cfg: dict) -> float:
    pc = cfg["pago_comprometido"]
    tasas, iva = pc["tasas_anuales"], pc["iva_intereses"]["valor"]
    total = 0.0
    for _, c in creditos.iterrows():
        if c["tipo_cuenta"] == "prestamo_personal":
            if (c["saldo_actual"] or 0) > 0:
                total += cuota_prestamo(
                    c["monto_original"],
                    c["plazo_meses"],
                    tasas["prestamo_personal"],
                    iva,
                )
        elif c["tipo_cuenta"] == "tarjeta_credito":
            total += pago_minimo_tarjeta(
                c["saldo_actual"],
                c["limite_credito"],
                tasas["tarjeta_credito"],
                iva,
                pc["pago_minimo_tarjeta"],
            )
    return total


# ---------------------------------------------------------------------------
# Lectura de Gold
# ---------------------------------------------------------------------------


def _datos_cliente(con, cliente_id: str, ventana_meses: int):
    meses = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT mes FROM gold_movimientos_mensuales ORDER BY mes"
        ).fetchall()
    ][-ventana_meses:]
    mov = con.execute(
        """
        SELECT mes, tipo_transaccion, categoria, monto_total
        FROM gold_movimientos_mensuales
        WHERE cliente_id = ? AND mes >= ?
        """,
        [cliente_id, meses[0] if meses else "0000-00"],
    ).fetchdf()
    feat = con.execute(
        "SELECT saldo_liquido_disponible FROM gold_features_cliente WHERE cliente_id = ?",
        [cliente_id],
    ).fetchone()
    creditos = con.execute(
        "SELECT * FROM gold_creditos_cliente WHERE cliente_id = ?", [cliente_id]
    ).fetchdf()
    return meses, mov, feat, creditos


def _serie(mov: pd.DataFrame, meses: list[str], filtro) -> pd.Series:
    sub = mov[filtro(mov)]
    return sub.groupby("mes")["monto_total"].sum().reindex(meses, fill_value=0.0)


def _metricas(meses, mov, saldo, creditos, cfg) -> tuple[dict, dict]:
    ingreso_mes = _serie(
        mov, meses, lambda m: m["tipo_transaccion"].isin(TIPOS_INGRESO)
    )
    depositos = _serie(mov, meses, lambda m: m["tipo_transaccion"] == "deposito").sum()
    salidas_mes = -_serie(
        mov, meses, lambda m: m["tipo_transaccion"].isin(TIPOS_SALIDA)
    )
    pagado_mes = -_serie(mov, meses, lambda m: m["categoria"] == "pago_deuda")

    ingreso_promedio = float(ingreso_mes.mean()) if len(meses) else 0.0
    total_ingreso = float(ingreso_mes.sum())
    variable = bool(
        total_ingreso > 0
        and depositos / total_ingreso
        > cfg["ingreso_variable"]["proporcion_depositos_min"]
    )
    con_ingreso = ingreso_mes[ingreso_mes > 0]
    if variable and len(con_ingreso):
        ingreso_referencia = float(con_ingreso.min())
    else:
        ingreso_referencia = ingreso_promedio
    gasto_mensual = float(salidas_mes.mean()) if len(meses) else 0.0

    comprometido = pago_comprometido(creditos, cfg)
    tarjetas = creditos[creditos["tipo_cuenta"] == "tarjeta_credito"]
    limite = float(tarjetas["limite_credito"].fillna(0).sum())

    metricas = {
        "ingreso_mensual_promedio": ingreso_promedio,
        "ingreso_variable": variable,
        "ingreso_referencia": ingreso_referencia,
        "gasto_mensual": gasto_mensual,
        "saldo_liquido": float(saldo or 0.0),
        "pago_comprometido": comprometido,
        "razon_pagos_deuda": (
            comprometido / ingreso_referencia
            if ingreso_referencia > 0
            else (0.0 if comprometido == 0 else None)
        ),
        "uso_tarjeta": (
            float(tarjetas["saldo_actual"].fillna(0).sum()) / limite
            if limite > 0
            else None
        ),
        "fondo_meses": (
            float(saldo or 0.0) / gasto_mensual if gasto_mensual > 0 else None
        ),
        "tasa_ahorro": (
            (ingreso_promedio - gasto_mensual) / ingreso_promedio
            if ingreso_promedio > 0
            else None
        ),
        "tiene_tarjeta": len(tarjetas) > 0,
        "tiene_prestamo": bool((creditos["tipo_cuenta"] == "prestamo_personal").any()),
    }

    n_recientes = cfg["cumplimiento"]["meses_recientes"]
    recientes = pagado_mes.tail(n_recientes)
    cumplimiento = {
        "meses_revisados": len(recientes),
        "meses_sin_pago": int((recientes <= 0).sum()) if comprometido > 0 else 0,
        "pagado_promedio": float(recientes.mean()) if len(recientes) else 0.0,
        "pagado_entre_comprometido": (
            float(recientes.mean()) / comprometido if comprometido > 0 else None
        ),
    }
    return metricas, cumplimiento


# ---------------------------------------------------------------------------
# Pasos
# ---------------------------------------------------------------------------


def _evaluar(metricas: dict, cfg: dict) -> dict[str, Paso]:
    u = cfg["umbrales"]
    fondo = metricas["fondo_meses"]
    razon = metricas["razon_pagos_deuda"]
    uso = metricas["uso_tarjeta"]
    ahorro = metricas["tasa_ahorro"]
    lim_deuda = u["pagos_deuda_max"]["valor"]
    lim_tarjeta = u["uso_tarjeta_max"]["valor"]
    fondo_min = u["fondo_emergencia"]["minimo_meses"]
    fondo_meta = u["fondo_emergencia"]["meta_meses"]
    ahorro_min = u["ahorro_min"]["valor"]
    pasos = {
        "pagos_deuda": Paso(
            "pagos_deuda",
            NOMBRES["pagos_deuda"],
            razon is not None and razon <= lim_deuda,
            razon,
            lim_deuda,
        ),
        "uso_tarjeta": Paso(
            "uso_tarjeta",
            NOMBRES["uso_tarjeta"],
            uso is None or uso <= lim_tarjeta,
            uso,
            lim_tarjeta,
            aplica=metricas["tiene_tarjeta"],
        ),
        "fondo_minimo": Paso(
            "fondo_minimo",
            NOMBRES["fondo_minimo"],
            fondo is not None and fondo >= fondo_min,
            fondo,
            fondo_min,
        ),
        "ahorro": Paso(
            "ahorro",
            NOMBRES["ahorro"],
            ahorro is not None and ahorro >= ahorro_min,
            ahorro,
            ahorro_min,
        ),
        "fondo_meta": Paso(
            "fondo_meta",
            NOMBRES["fondo_meta"],
            fondo is not None and fondo >= fondo_meta,
            fondo,
            fondo_meta,
        ),
    }
    previos = [pasos[p] for p in cfg["orden"] if p != "invertir"]
    pasos["invertir"] = Paso(
        "invertir", NOMBRES["invertir"], all(p.cumple for p in previos), None, None
    )
    return pasos


def _lectura(paso: str, m: dict, cumplimiento: dict, cfg: dict) -> str:
    """El paso en una o dos frases para el cliente. Montos con formato_mxn
    estilo cliente."""
    u = cfg["umbrales"]
    pesos = lambda v: formato_mxn(v, estilo="cliente")  # noqa: E731
    referencia = (
        f"tu mes de menor ingreso ({pesos(m['ingreso_referencia'])})"
        if m["ingreso_variable"]
        else f"tu ingreso de {pesos(m['ingreso_referencia'])} al mes"
    )
    if paso == "pagos_deuda":
        if m["razon_pagos_deuda"] is None:
            texto = (
                f"Tienes pagos de deuda de {pesos(m['pago_comprometido'])} al mes y no "
                "registramos ingreso: el primer paso es que tus pagos no pasen del "
                f"{u['pagos_deuda_max']['valor'] * 100:.0f} % de lo que ganas."
            )
        else:
            maximo = u["pagos_deuda_max"]["valor"] * m["ingreso_referencia"]
            texto = (
                f"Tus pagos de deuda son {pesos(m['pago_comprometido'])} al mes, el "
                f"{m['razon_pagos_deuda'] * 100:.0f} % de {referencia}. Para el "
                f"siguiente paso deberían quedar en {pesos(maximo)} o menos."
            )
    elif paso == "uso_tarjeta":
        texto = (
            f"Usas el {m['uso_tarjeta'] * 100:.0f} % del límite de tu tarjeta. "
            f"Bajarlo al {u['uso_tarjeta_max']['valor'] * 100:.0f} % es el siguiente paso."
        )
    elif paso in ("fondo_minimo", "fondo_meta"):
        meta = u["fondo_emergencia"][
            "minimo_meses" if paso == "fondo_minimo" else "meta_meses"
        ]
        faltan = max(meta * m["gasto_mensual"] - m["saldo_liquido"], 0.0)
        meses = m["fondo_meses"] or 0.0
        texto = (
            f"Tu saldo de {pesos(m['saldo_liquido'])} cubre {meses:.1f} meses de tus "
            f"gastos. Para {meta} meses te faltan {pesos(faltan)}."
        )
    elif paso == "ahorro":
        ahorro = m["tasa_ahorro"]
        if ahorro is None:
            texto = "No registramos ingreso para medir cuánto ahorras."
        else:
            meta = u["ahorro_min"]["valor"] * m["ingreso_mensual_promedio"]
            texto = (
                f"Ahorras el {ahorro * 100:.0f} % de tu ingreso. La meta es al menos "
                f"el {u['ahorro_min']['valor'] * 100:.0f} %, unos {pesos(meta)} al mes."
            )
    else:
        texto = (
            "Tus deudas, tu tarjeta, tu fondo de emergencia y tu ahorro están en "
            "orden: ya puedes pensar en invertir."
        )
    if cumplimiento["meses_sin_pago"]:
        texto += (
            f" En {cumplimiento['meses_sin_pago']} de los últimos "
            f"{cumplimiento['meses_revisados']} meses no registramos pagos de tus "
            "deudas."
        )
    return texto


def calcular(
    ruta_gold: str, cliente_id: str, ventana_meses: int, cfg: dict | None = None
) -> Escalera:
    cfg = cfg or cargar_config()
    con = duckdb.connect(ruta_gold, read_only=True)
    try:
        meses, mov, feat, creditos = _datos_cliente(con, cliente_id, ventana_meses)
    finally:
        con.close()
    if feat is None:
        raise ValueError(f"{cliente_id} no esta en gold_features_cliente")
    metricas, cumplimiento = _metricas(meses, mov, feat[0], creditos, cfg)
    pasos = _evaluar(metricas, cfg)
    orden = cfg["orden"]
    actual = next((p for p in orden if not pasos[p].cumple), "invertir")
    supuestos = []
    if metricas["ingreso_variable"]:
        supuestos.append(
            "Ingreso variable: la deuda se compara con el mes de menor ingreso de "
            "los últimos meses (CONDUSEF)."
        )
    if metricas["tiene_tarjeta"] or metricas["tiene_prestamo"]:
        tasas = cfg["pago_comprometido"]["tasas_anuales"]
        supuestos.append(
            "Pago de deuda estimado con las tasas del catálogo (tarjeta "
            f"{tasas['tarjeta_credito'] * 100:.0f} %, préstamo "
            f"{tasas['prestamo_personal'] * 100:.0f} %) más IVA, y el pago mínimo "
            "de tarjeta de Banxico."
        )
    return Escalera(
        cliente_id=cliente_id,
        paso_actual=actual,
        numero_paso=orden.index(actual) + 1,
        pasos=[pasos[p] for p in orden],
        metricas=metricas,
        cumplimiento=cumplimiento,
        lectura=_lectura(actual, metricas, cumplimiento, cfg),
        supuestos=supuestos,
    )

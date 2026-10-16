"""
src/common/escenario_deriva.py

Escenario de deriva del generador sintetico: un deterioro gradual de una
parte de la cartera a partir de cierta entrega mensual, para que el
monitoreo de deriva (monitor_drift.py) tenga algo que medir en una
instancia larga. Se declara en config/sesion.yaml (escenario_deriva) y
esta apagado por defecto.

Reglas de diseno:

- Solo afecta a los meses de entrega (posteriores al corte base). Los meses
  base, los clientes, las cuentas y los CETES no cambian, asi que la
  referencia del PSI creada sobre la sesion base sigue siendo valida.
- No consume numeros del generador aleatorio global: los efectos son
  factores y umbrales que se aplican despues de los sorteos que el
  generador ya hace. Las fechas, las categorias y el numero de compras de
  un mes salen iguales con o sin escenario; cambian los montos y que pagos
  de deuda se hacen.
- Los clientes afectados se sortean con un generador propio, derivado de
  la semilla de la sesion: mismo escenario y misma semilla, mismos
  clientes.
- Con el escenario apagado el generador no pasa por aqui, y la sesion sale
  byte a byte igual que sin esta funcionalidad.

Efectos, compuestos por cada mes de escenario k (1 en el primer mes
afectado):
    ingreso   x (1 - caida_ingreso_mensual) ** k
    gasto     x (1 + aumento_gasto_mensual) ** k
    puntualidad de pago de cada deuda  - caida_puntualidad_mensual * k (minimo 0)
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class EscenarioDeriva:
    desde_entrega: int
    proporcion_clientes: float
    caida_ingreso_mensual: float
    aumento_gasto_mensual: float
    caida_puntualidad_mensual: float

    def como_dict(self) -> dict:
        return asdict(self)


def cargar_escenario(
    crudo: dict | None, origen: str = "escenario_deriva"
) -> EscenarioDeriva | None:
    """Valida la seccion escenario_deriva de la config. Devuelve None si no
    existe o si activo es false: en ese caso el generador se comporta
    exactamente como sin escenario."""
    if not crudo or not crudo.get("activo", False):
        return None
    try:
        escenario = EscenarioDeriva(
            desde_entrega=int(crudo["desde_entrega"]),
            proporcion_clientes=float(crudo["proporcion_clientes"]),
            caida_ingreso_mensual=float(crudo["caida_ingreso_mensual"]),
            aumento_gasto_mensual=float(crudo["aumento_gasto_mensual"]),
            caida_puntualidad_mensual=float(crudo["caida_puntualidad_mensual"]),
        )
    except KeyError as faltante:
        raise ValueError(f"{origen}: falta la llave {faltante}") from None

    for nombre, valor in escenario.como_dict().items():
        if not math.isfinite(valor):
            raise ValueError(
                f"{origen}: {nombre} debe ser un numero finito, no {valor}"
            )
    if escenario.desde_entrega < 1:
        raise ValueError(
            f"{origen}: desde_entrega debe ser >= 1 (los meses base no se alteran)"
        )
    if not 0.0 < escenario.proporcion_clientes <= 1.0:
        raise ValueError(f"{origen}: proporcion_clientes debe estar en (0, 1]")
    if not 0.0 <= escenario.caida_ingreso_mensual < 1.0:
        raise ValueError(f"{origen}: caida_ingreso_mensual debe estar en [0, 1)")
    if not 0.0 <= escenario.aumento_gasto_mensual <= 1.0:
        raise ValueError(f"{origen}: aumento_gasto_mensual debe estar en [0, 1]")
    if not 0.0 <= escenario.caida_puntualidad_mensual <= 1.0:
        raise ValueError(f"{origen}: caida_puntualidad_mensual debe estar en [0, 1]")
    return escenario


def huella(escenario: EscenarioDeriva) -> str:
    """8 caracteres que identifican el escenario: van en el id de la sesion,
    para que una sesion con escenario no se confunda con la base ni con
    otra de escenario distinto."""
    canonico = json.dumps(escenario.como_dict(), sort_keys=True)
    return hashlib.sha256(canonico.encode()).hexdigest()[:8]


def clientes_afectados(
    cliente_ids, escenario: EscenarioDeriva, semilla: int
) -> frozenset:
    """Sorteo de los clientes afectados con un generador propio: no toca el
    estado global de random/NumPy que usa el resto del generador."""
    digest = hashlib.sha256(f"{semilla}:escenario_deriva".encode()).hexdigest()
    rng = np.random.default_rng(int(digest[:8], 16))
    ids = sorted(cliente_ids)
    n = round(len(ids) * escenario.proporcion_clientes)
    return (
        frozenset(rng.choice(ids, size=n, replace=False).tolist()) if n else frozenset()
    )


def meses_de_escenario(
    escenario: EscenarioDeriva, anio: int, mes: int, ultimo_mes_base: tuple[int, int]
) -> int:
    """k: cuantos meses lleva el escenario en (anio, mes). 0 si el mes no
    esta afectado (meses base y entregas anteriores a desde_entrega)."""
    entrega = (anio * 12 + mes) - (ultimo_mes_base[0] * 12 + ultimo_mes_base[1])
    return max(0, entrega - escenario.desde_entrega + 1)


def efectos(escenario: EscenarioDeriva, k: int) -> dict:
    """Factores del mes k para un cliente afectado."""
    return {
        "factor_ingreso": (1.0 - escenario.caida_ingreso_mensual) ** k,
        "factor_gasto": (1.0 + escenario.aumento_gasto_mensual) ** k,
        "caida_puntualidad": escenario.caida_puntualidad_mensual * k,
    }

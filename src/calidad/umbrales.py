"""
src/calidad/umbrales.py

Umbral de cuarentena por entidad en Silver. La cuarentena aisla filas
invalidas sin detener el pipeline, pero no distingue "llegaron unas
cuantas CURP mal capturadas" de "la entrega de este mes llego rota". En el
segundo caso Silver se sobrescribia con lo poco que paso y Gold calculaba
KPIs sobre una fraccion de los datos, sin aviso.

Dos niveles, declarados en config/business_rules.yaml (umbrales_cuarentena):

- aviso: la tasa de cuarentena (filas en cuarentena / filas que entraron a
  validar) lo alcanza. Se registra una alerta y la corrida sigue.
- bloqueo: la tasa lo alcanza. Se registra la alerta, se escribe la
  cuarentena para diagnosticar, NO se escribe Silver y la tarea falla; el
  manejo de errores de transform_silver.py restaura las tablas que la
  corrida alcanzo a escribir y Airflow manda su alerta de tarea fallida.

Con menos de filas_minimas filas no se bloquea: con 8 filas, una mala ya
es 12.5 % y el porcentaje no dice nada de la fuente. Solo se avisa.

La alerta lleva la entidad, conteos, tasa y nombres de reglas, nunca
valores (mismo criterio que src/calidad/registro.py).

Sin Spark: recibe conteos, asi que las pruebas no levantan una sesion.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

OK = "ok"
AVISO = "aviso"
BLOQUEO = "bloqueo"

ARCHIVO_ALERTAS = "alertas_cuarentena.jsonl"


class CuarentenaExcedida(RuntimeError):
    """La tasa de cuarentena de una entidad alcanzo su umbral de bloqueo."""


@dataclass(frozen=True)
class Umbral:
    aviso: float
    bloqueo: float | None  # None: la entidad solo avisa
    filas_minimas: int


@dataclass(frozen=True)
class Evaluacion:
    entidad: str
    nivel: str
    filas_entrada: int
    filas_cuarentena: int
    tasa: float
    umbral: Umbral
    reglas_principales: list[tuple[str, int]]


def _validar(nombre: str, umbral: Umbral) -> Umbral:
    if not 0 < umbral.aviso <= 1:
        raise ValueError(f"umbrales_cuarentena.{nombre}.aviso debe estar en (0, 1]")
    if umbral.bloqueo is not None and not umbral.aviso <= umbral.bloqueo <= 1:
        raise ValueError(
            f"umbrales_cuarentena.{nombre}.bloqueo debe estar entre aviso y 1"
        )
    if umbral.filas_minimas < 0:
        raise ValueError(f"umbrales_cuarentena.{nombre}.filas_minimas no puede ser < 0")
    return umbral


def cargar_umbrales(reglas: dict) -> dict[str, Umbral]:
    """Lee la seccion umbrales_cuarentena: un bloque default y, opcional, uno
    por entidad que reemplaza solo las claves que declara. Devuelve un
    diccionario con "default" y cada entidad declarada."""
    seccion = reglas.get("umbrales_cuarentena")
    if not seccion or "default" not in seccion:
        raise ValueError(
            "config/business_rules.yaml: falta umbrales_cuarentena.default"
        )
    base = seccion["default"]
    claves = {"aviso", "bloqueo", "filas_minimas"}
    umbrales = {}
    for nombre, valores in seccion.items():
        desconocidas = set(valores or {}) - claves
        if desconocidas:
            raise ValueError(
                f"umbrales_cuarentena.{nombre}: claves desconocidas {sorted(desconocidas)}"
            )
        combinado = {**base, **(valores or {})}
        umbrales[nombre] = _validar(
            nombre,
            Umbral(
                aviso=float(combinado["aviso"]),
                bloqueo=(
                    None
                    if combinado["bloqueo"] is None
                    else float(combinado["bloqueo"])
                ),
                filas_minimas=int(combinado["filas_minimas"]),
            ),
        )
    return umbrales


def umbral_de(entidad: str, umbrales: dict[str, Umbral]) -> Umbral:
    return umbrales.get(entidad, umbrales["default"])


def evaluar(
    entidad: str,
    filas_entrada: int,
    filas_cuarentena: int,
    violaciones_por_regla: dict,
    umbral: Umbral,
) -> Evaluacion:
    tasa = filas_cuarentena / filas_entrada if filas_entrada else 0.0
    if (
        umbral.bloqueo is not None
        and tasa >= umbral.bloqueo
        and filas_entrada >= umbral.filas_minimas
    ):
        nivel = BLOQUEO
    elif filas_cuarentena and tasa >= umbral.aviso:
        nivel = AVISO
    else:
        nivel = OK
    principales = sorted(violaciones_por_regla.items(), key=lambda kv: (-kv[1], kv[0]))
    return Evaluacion(
        entidad=entidad,
        nivel=nivel,
        filas_entrada=filas_entrada,
        filas_cuarentena=filas_cuarentena,
        tasa=tasa,
        umbral=umbral,
        reglas_principales=[(r, int(n)) for r, n in principales[:3]],
    )


def registro_de_alerta(
    evaluacion: Evaluacion, run_id: str, ahora: datetime | None = None
) -> dict:
    ahora = ahora or datetime.now(timezone.utc)
    return {
        "fecha": ahora.isoformat(timespec="seconds"),
        "run_id": run_id,
        "entidad": evaluacion.entidad,
        "nivel": evaluacion.nivel,
        "filas_entrada": evaluacion.filas_entrada,
        "filas_cuarentena": evaluacion.filas_cuarentena,
        "tasa": round(evaluacion.tasa, 6),
        "umbral_aviso": evaluacion.umbral.aviso,
        "umbral_bloqueo": evaluacion.umbral.bloqueo,
        # Nombres de regla y conteos; nunca valores.
        "reglas_principales": [
            {"regla": r, "filas": n} for r, n in evaluacion.reglas_principales
        ],
    }


def escribir_alerta(registro: dict, ruta: str | Path) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def mensaje(evaluacion: Evaluacion) -> str:
    reglas = ", ".join(f"{r} ({n})" for r, n in evaluacion.reglas_principales)
    return (
        f"[{evaluacion.entidad}] {evaluacion.nivel}: "
        f"{evaluacion.filas_cuarentena:,} de {evaluacion.filas_entrada:,} filas en "
        f"cuarentena ({evaluacion.tasa:.2%}); umbral de aviso "
        f"{evaluacion.umbral.aviso:.2%}, de bloqueo "
        + (
            "sin bloqueo"
            if evaluacion.umbral.bloqueo is None
            else f"{evaluacion.umbral.bloqueo:.2%}"
        )
        + (f". Reglas: {reglas}" if reglas else "")
    )

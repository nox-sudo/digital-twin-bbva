"""
asistente/herramientas.py

Herramientas del asistente conversacional: las unicas funciones con las que
el modelo de lenguaje obtiene cifras. El modelo no calcula ni recuerda
numeros; elige una herramienta y sus argumentos, Python la ejecuta sobre
Gold (DuckDB, solo lectura) y el modelo redacta la respuesta con lo que
devolvio.

Reglas de diseno:

  - El cliente lo fija la interfaz, no el modelo. Contexto.cliente_id sale
    de la seleccion en el dashboard; ningun esquema expone cliente_id, asi
    que el modelo no puede consultar a otro cliente ni inventar un id.
  - Mismas consultas que el dashboard (dashboard/datos.py) y el simulador
    (src/simulacion/montecarlo.py), con la misma configuracion: el asistente
    da las mismas cifras que las vistas.
  - Cada herramienta devuelve un dict serializable a JSON. Un argumento
    invalido o un dato que no existe se devuelve como {"error": ...} en vez
    de lanzar, para que el modelo lo explique en lugar de inventar.

Sin Streamlit ni Spark: lo usan igual la pagina del asistente, las pruebas
y la evaluacion.
"""

import math
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from dashboard import datos
from src.simulacion import montecarlo as mc

RAIZ = Path(__file__).resolve().parents[1]

PATRON_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Variables del feature store que se pueden comparar contra el portafolio.
VARIABLES_COMPARABLES = [
    "ingreso_mensual_promedio",
    "gasto_mensual_total",
    "capacidad_ahorro",
    "saldo_liquido_disponible",
    "ratio_endeudamiento",
    "uso_linea_credito",
    "estabilidad_ingreso",
    "probabilidad_impago",
]

# Mismos rangos que los controles de la pagina del simulador.
PLAZOS_ANIOS = [5, 10, 15, 20, 25, 30]
ENGANCHE_PCT = (0.05, 0.40)
TASA_ANUAL = (0.06, 0.18)
PRECIO_MAXIMO = 20_000_000


class ErrorHerramienta(ValueError):
    """Argumento invalido o dato inexistente: se devuelve al modelo como
    {"error": mensaje}."""


@dataclass(frozen=True)
class Contexto:
    cliente_id: str
    ruta_gold: str
    ruta_resumen: str
    bandas: list
    ventana_meses: int
    simulacion: dict

    @classmethod
    def desde_config(cls, cliente_id: str, raiz: Path = RAIZ) -> "Contexto":
        """Las rutas y parametros que usa el dashboard."""
        dashboard = datos.cargar_config(str(raiz / "config" / "dashboard.yaml"))
        features = datos.cargar_config(str(raiz / "config" / "features.yaml"))
        with open(raiz / "config" / "simulacion.yaml", encoding="utf-8") as f:
            simulacion = yaml.safe_load(f)
        return cls(
            cliente_id=cliente_id,
            ruta_gold=str(raiz / dashboard["gold_duckdb"]),
            ruta_resumen=str(raiz / dashboard["resumen_portafolio"]),
            bandas=dashboard["bandas_riesgo"],
            ventana_meses=int(features["ventana_meses"]),
            simulacion=simulacion,
        )


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _numero(valor, decimales: int = 2):
    """float redondeado, o None si falta. Nunca NaN: no es JSON valido."""
    if valor is None:
        return None
    valor = float(valor)
    if math.isnan(valor):
        return None
    return round(valor, decimales)


def _a_numero(nombre: str, valor) -> float:
    """Los modelos a veces mandan numeros como texto ("900000")."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        raise ErrorHerramienta(f"{nombre} debe ser un numero: {valor!r}") from None
    if math.isnan(numero):
        raise ErrorHerramienta(f"{nombre} debe ser un numero: {valor!r}")
    return numero


def _portafolio(ctx: Contexto) -> pd.DataFrame:
    return datos.portafolio(ctx.ruta_gold, ctx.bandas)


def _fila_cliente(ctx: Contexto, df: pd.DataFrame) -> pd.Series:
    filas = df[df["cliente_id"] == ctx.cliente_id]
    if filas.empty:
        raise ErrorHerramienta(f"{ctx.cliente_id} no esta en gold_features_cliente")
    return filas.iloc[0]


def _meses_disponibles(ctx: Contexto) -> list[str]:
    with datos.conexion_gold(ctx.ruta_gold) as con:
        filas = con.execute(
            "SELECT DISTINCT mes FROM gold_movimientos_mensuales "
            "WHERE cliente_id = ? ORDER BY mes",
            [ctx.cliente_id],
        ).fetchall()
    return [f[0] for f in filas]


def _rango(ctx: Contexto, desde: str | None, hasta: str | None) -> tuple[str, str]:
    """Rango de meses validado. Sin limites, la ventana de las features
    (los ultimos ventana_meses meses hasta el corte), la misma de las
    metricas del cliente."""
    meses = _meses_disponibles(ctx)
    if not meses:
        raise ErrorHerramienta(f"{ctx.cliente_id} no tiene movimientos en Gold")
    for nombre, valor in (("desde", desde), ("hasta", hasta)):
        if valor is not None and not PATRON_MES.match(str(valor)):
            raise ErrorHerramienta(f"{nombre} debe tener formato AAAA-MM: {valor!r}")
    hasta = hasta or meses[-1]
    desde = desde or datos.inicio_ventana(meses[-1], ctx.ventana_meses)
    if desde > hasta:
        raise ErrorHerramienta(f"desde ({desde}) es posterior a hasta ({hasta})")
    if hasta < meses[0] or desde > meses[-1]:
        raise ErrorHerramienta(
            f"Sin datos entre {desde} y {hasta}: hay movimientos de "
            f"{meses[0]} a {meses[-1]}"
        )
    return max(desde, meses[0]), min(hasta, meses[-1])


# ---------------------------------------------------------------------------
# Herramientas
# ---------------------------------------------------------------------------


def perfil_cliente(ctx: Contexto) -> dict:
    """Perfil, salud financiera y riesgo del cliente, como en la vista
    Cliente."""
    fila = _fila_cliente(ctx, _portafolio(ctx))
    productos = ["cuenta digital"]
    if fila["tiene_tarjeta_credito"]:
        productos.append("tarjeta de credito")
    if fila["tiene_prestamo_personal"]:
        productos.append("prestamo personal")
    return {
        "cliente_id": ctx.cliente_id,
        "fecha_corte": str(pd.Timestamp(fila["fecha_corte"]).date()),
        "probabilidad_impago": _numero(fila["probabilidad_impago"], 4),
        "banda_riesgo": fila["banda_riesgo"],
        "ventana_meses": ctx.ventana_meses,
        "ingreso_mensual_promedio": _numero(fila["ingreso_mensual_promedio"]),
        "gasto_mensual_total": _numero(fila["gasto_mensual_total"]),
        "capacidad_ahorro": _numero(fila["capacidad_ahorro"]),
        "saldo_liquido_disponible": _numero(fila["saldo_liquido_disponible"]),
        "ratio_endeudamiento": _numero(fila["ratio_endeudamiento"], 4),
        "uso_linea_credito": _numero(fila["uso_linea_credito"], 4),
        "estabilidad_ingreso": _numero(fila["estabilidad_ingreso"], 4),
        "categoria_gasto_dominante": fila["categoria_gasto_dominante"],
        "edad_anios": _numero(fila["edad_anios"], 0),
        "antiguedad_anios": _numero(fila["antiguedad_dias"] / 365, 1),
        "productos": productos,
    }


def gasto_por_categoria(
    ctx: Contexto,
    desde: str | None = None,
    hasta: str | None = None,
    categoria: str | None = None,
) -> dict:
    """Salidas del cliente por categoria en un rango de meses. Con
    categoria, el detalle mes a mes de esa categoria."""
    desde, hasta = _rango(ctx, desde, hasta)
    with datos.conexion_gold(ctx.ruta_gold) as con:
        df = con.execute(
            """
            SELECT mes, categoria, -SUM(monto_total) AS total
            FROM gold_movimientos_mensuales
            WHERE cliente_id = ? AND monto_total < 0 AND mes BETWEEN ? AND ?
            GROUP BY mes, categoria
            """,
            [ctx.cliente_id, desde, hasta],
        ).fetchdf()
    total = float(df["total"].sum())
    resultado = {"desde": desde, "hasta": hasta, "total_salidas": _numero(total)}
    if categoria is None:
        por_categoria = (
            df.groupby("categoria")["total"].sum().sort_values(ascending=False)
        )
        resultado["categorias"] = [
            {
                "categoria": nombre,
                "total": _numero(monto),
                "proporcion": _numero(monto / total if total else None, 4),
            }
            for nombre, monto in por_categoria.items()
        ]
        return resultado
    if categoria not in set(df["categoria"]):
        raise ErrorHerramienta(
            f"Sin salidas en la categoria {categoria!r} entre {desde} y {hasta}. "
            f"Categorias con salidas: {sorted(set(df['categoria']))}"
        )
    de_categoria = df[df["categoria"] == categoria].sort_values("mes")
    subtotal = float(de_categoria["total"].sum())
    resultado.update(
        {
            "categoria": categoria,
            "total_categoria": _numero(subtotal),
            "proporcion": _numero(subtotal / total if total else None, 4),
            "por_mes": [
                {"mes": m, "total": _numero(t)}
                for m, t in zip(de_categoria["mes"], de_categoria["total"])
            ],
        }
    )
    return resultado


def flujo_mensual(
    ctx: Contexto, desde: str | None = None, hasta: str | None = None
) -> dict:
    """Entradas, salidas y neto del cliente por mes en un rango."""
    desde, hasta = _rango(ctx, desde, hasta)
    df = datos.flujo_mensual(ctx.ruta_gold, ctx.cliente_id)
    df = df[(df["mes"] >= desde) & (df["mes"] <= hasta)]
    return {
        "desde": desde,
        "hasta": hasta,
        "meses": [
            {
                "mes": f["mes"],
                "entradas": _numero(f["entradas"]),
                "salidas": _numero(f["salidas"]),
                "neto": _numero(f["neto"]),
            }
            for _, f in df.iterrows()
        ],
        "neto_promedio": _numero(df["neto"].mean()),
        "meses_con_neto_negativo": int((df["neto"] < 0).sum()),
    }


def comparar_con_portafolio(ctx: Contexto, variables: list | None = None) -> dict:
    """Donde queda el cliente en el portafolio: para cada variable, su
    valor, la mediana del portafolio y la proporcion de clientes con un
    valor menor o igual."""
    variables = variables or VARIABLES_COMPARABLES
    desconocidas = [v for v in variables if v not in VARIABLES_COMPARABLES]
    if desconocidas:
        raise ErrorHerramienta(
            f"Variables no comparables: {desconocidas}. "
            f"Disponibles: {VARIABLES_COMPARABLES}"
        )
    df = _portafolio(ctx)
    fila = _fila_cliente(ctx, df)
    return {
        "clientes_en_portafolio": len(df),
        "comparacion": [
            {
                "variable": v,
                "valor_cliente": _numero(fila[v], 4),
                "mediana_portafolio": _numero(df[v].median(), 4),
                "proporcion_menor_o_igual": _numero(
                    datos.percentil_en_portafolio(df, v, fila[v]), 4
                ),
            }
            for v in variables
        ],
    }


def simular_hipoteca(
    ctx: Contexto,
    precio: float,
    enganche_pct: float | None = None,
    plazo_anios: int | None = None,
    tasa_anual: float | None = None,
    renta_reemplazada: float = 0.0,
) -> dict:
    """Mismo calculo que la vista del simulador: riesgo de deficit con y sin
    la hipoteca sobre las mismas muestras, cuota, capacidad hipotecaria y
    meses para reunir el enganche. Sin un parametro, el de
    config/simulacion.yaml."""
    cfg = ctx.simulacion
    precio = _a_numero("precio", precio)
    enganche_pct = _a_numero(
        "enganche_pct",
        cfg["hipoteca"]["enganche_pct"] if enganche_pct is None else enganche_pct,
    )
    plazo_anios = _a_numero(
        "plazo_anios",
        cfg["hipoteca"]["plazo_anios"] if plazo_anios is None else plazo_anios,
    )
    tasa_anual = _a_numero(
        "tasa_anual",
        cfg["hipoteca"]["tasa_anual"] if tasa_anual is None else tasa_anual,
    )
    renta_reemplazada = _a_numero("renta_reemplazada", renta_reemplazada)
    if not 0 < precio <= PRECIO_MAXIMO:
        raise ErrorHerramienta(f"precio debe estar entre 0 y {PRECIO_MAXIMO:,}")
    if not ENGANCHE_PCT[0] <= enganche_pct <= ENGANCHE_PCT[1]:
        raise ErrorHerramienta(
            f"enganche_pct debe estar entre {ENGANCHE_PCT[0]} y {ENGANCHE_PCT[1]} "
            "(fraccion del precio, 0.10 = 10 %)"
        )
    if plazo_anios not in PLAZOS_ANIOS:
        raise ErrorHerramienta(f"plazo_anios debe ser uno de {PLAZOS_ANIOS}")
    if not TASA_ANUAL[0] <= tasa_anual <= TASA_ANUAL[1]:
        raise ErrorHerramienta(
            f"tasa_anual debe estar entre {TASA_ANUAL[0]} y {TASA_ANUAL[1]} "
            "(fraccion, 0.11 = 11 %)"
        )
    if renta_reemplazada < 0:
        raise ErrorHerramienta("renta_reemplazada no puede ser negativa")

    choque = cfg["choque_ingreso"]
    supuestos = mc.Supuestos(
        meses=int(cfg["simulacion"]["meses"]),
        trayectorias=int(cfg["simulacion"]["trayectorias"]),
        semilla=int(cfg["simulacion"]["semilla"]),
        prob_choque_mensual=float(choque["probabilidad_mensual"]),
        duracion_choque=tuple(int(x) for x in choque["duracion_meses"]),
    )
    hipoteca = mc.Hipoteca(
        precio=float(precio),
        tasa_anual=float(tasa_anual),
        plazo_anios=int(plazo_anios),
        enganche_pct=float(enganche_pct),
        gastos_iniciales_pct=float(cfg["hipoteca"]["gastos_iniciales_pct"]),
        renta_reemplazada=float(renta_reemplazada),
    )
    try:
        historial = mc.historial_cliente(ctx.ruta_gold, ctx.cliente_id)
        saldo = mc.saldo_inicial_cliente(ctx.ruta_gold, ctx.cliente_id)
    except ValueError as e:
        raise ErrorHerramienta(str(e)) from e
    muestras = mc.muestrear(len(historial.meses), supuestos)
    sin = mc.resumir(mc.simular(historial, saldo, muestras))
    con = mc.evaluar_hipoteca(historial, saldo, hipoteca, muestras)
    incremento = float(cfg["incremento_deficit_maximo"])
    meses_enganche = mc.meses_para_juntar(hipoteca.pago_inicial, historial, saldo)
    return {
        "precio": _numero(precio),
        "enganche_pct": _numero(enganche_pct, 4),
        "plazo_anios": int(plazo_anios),
        "tasa_anual": _numero(tasa_anual, 4),
        "renta_reemplazada": _numero(renta_reemplazada),
        "cuota_mensual": _numero(hipoteca.cuota_mensual),
        "pago_inicial": _numero(hipoteca.pago_inicial),
        "meses_simulados": supuestos.meses,
        "trayectorias": supuestos.trayectorias,
        "probabilidad_deficit_sin_hipoteca": _numero(sin.probabilidad_deficit, 4),
        "probabilidad_deficit_con_hipoteca": _numero(con.probabilidad_deficit, 4),
        "capacidad_hipotecaria": _numero(
            mc.capacidad_maxima(historial, saldo, hipoteca, muestras, incremento)
        ),
        "incremento_deficit_maximo": incremento,
        "ahorro_mensual_promedio": _numero(historial.neto.mean()),
        "meses_para_reunir_pago_inicial": _numero(meses_enganche, 1),
    }


def resumen_portafolio(ctx: Contexto) -> dict:
    """Cifras de cabecera del portafolio, su cambio contra la base y la
    distribucion por banda de riesgo, como en la vista Portafolio."""
    df = _portafolio(ctx)
    cifras = datos.resumen_portafolio(df)
    base = datos.resumen_base(ctx.ruta_resumen, cifras["fecha_corte"])
    bandas = df["banda_riesgo"].value_counts()
    resultado = {
        "clientes": cifras["clientes"],
        "fecha_corte": str(pd.Timestamp(cifras["fecha_corte"]).date()),
        "ingreso_mediano": _numero(cifras["ingreso_mediano"]),
        "proporcion_ahorro_negativo": _numero(cifras["pct_ahorro_negativo"], 4),
        "probabilidad_media": _numero(cifras["probabilidad_media"], 4),
        "clientes_por_banda": {b: int(n) for b, n in bandas.items()},
        "base": None,
    }
    if base is not None:
        resultado["base"] = {
            "fecha_corte": str(pd.Timestamp(base["fecha_corte"]).date()),
            "ingreso_mediano": _numero(base["ingreso_mediano"]),
            "proporcion_ahorro_negativo": _numero(base["pct_ahorro_negativo"], 4),
            "probabilidad_media": _numero(base["probabilidad_media"], 4),
        }
    return resultado


# ---------------------------------------------------------------------------
# Registro: lo que ve el modelo
# ---------------------------------------------------------------------------

_MES = {"type": "string", "description": "Mes en formato AAAA-MM"}

HERRAMIENTAS = {
    "perfil_cliente": {
        "funcion": perfil_cliente,
        "descripcion": "Perfil, salud financiera (ingreso, gasto, capacidad de "
        "ahorro, saldo, endeudamiento) y riesgo de impago del cliente.",
        "parametros": {},
        "requeridos": [],
    },
    "gasto_por_categoria": {
        "funcion": gasto_por_categoria,
        "descripcion": "Salidas del cliente por categoria en un rango de meses "
        "(por defecto, los ultimos 12). Con categoria, el detalle mes a mes. "
        "Categorias: comida, transporte, entretenimiento, servicios, salud, "
        "educacion, hogar, ropa, suscripciones, otros, pago_deuda, efectivo, "
        "transferencia_personal.",
        "parametros": {
            "desde": _MES,
            "hasta": _MES,
            "categoria": {"type": "string", "description": "Una categoria"},
        },
        "requeridos": [],
    },
    "flujo_mensual": {
        "funcion": flujo_mensual,
        "descripcion": "Entradas, salidas y neto del cliente mes a mes en un "
        "rango de meses (por defecto, los ultimos 12).",
        "parametros": {"desde": _MES, "hasta": _MES},
        "requeridos": [],
    },
    "comparar_con_portafolio": {
        "funcion": comparar_con_portafolio,
        "descripcion": "Compara al cliente con el resto del portafolio: mediana "
        "y proporcion de clientes con un valor menor o igual.",
        "parametros": {
            "variables": {
                "type": "array",
                "items": {"type": "string", "enum": VARIABLES_COMPARABLES},
                "description": "Variables a comparar; por defecto todas",
            }
        },
        "requeridos": [],
    },
    "simular_hipoteca": {
        "funcion": simular_hipoteca,
        "descripcion": "Simula contratar una hipoteca: riesgo de quedar en saldo "
        "negativo con y sin ella, cuota mensual, capacidad hipotecaria y meses "
        "para reunir el enganche.",
        "parametros": {
            "precio": {"type": "number", "description": "Precio de la vivienda (MXN)"},
            "enganche_pct": {
                "type": "number",
                "description": "Fraccion del precio, entre 0.05 y 0.40",
            },
            "plazo_anios": {"type": "integer", "enum": PLAZOS_ANIOS},
            "tasa_anual": {
                "type": "number",
                "description": "Tasa anual como fraccion, entre 0.06 y 0.18",
            },
            "renta_reemplazada": {
                "type": "number",
                "description": "Renta mensual que deja de pagar (MXN)",
            },
        },
        "requeridos": ["precio"],
    },
    "resumen_portafolio": {
        "funcion": resumen_portafolio,
        "descripcion": "Cifras del portafolio completo (ingreso mediano, "
        "proporcion que gasta mas de lo que gana, probabilidad media de "
        "impago, clientes por banda) y su cambio contra la sesion base.",
        "parametros": {},
        "requeridos": [],
    },
}


def esquemas() -> list[dict]:
    """Esquemas de las herramientas en el formato de llamadas a funciones
    (el que aceptan Ollama y LangChain). Ninguno incluye cliente_id."""
    return [
        {
            "type": "function",
            "function": {
                "name": nombre,
                "description": h["descripcion"],
                "parameters": {
                    "type": "object",
                    "properties": h["parametros"],
                    "required": h["requeridos"],
                },
            },
        }
        for nombre, h in HERRAMIENTAS.items()
    ]


def ejecutar(nombre: str, argumentos: dict | None, ctx: Contexto) -> dict:
    """Ejecuta la herramienta que pidio el modelo. Un nombre o argumento
    que no esta en el esquema, o cualquier ErrorHerramienta, vuelve como
    {"error": ...}. cliente_id siempre sale del contexto."""
    if nombre not in HERRAMIENTAS:
        return {"error": f"No existe la herramienta {nombre!r}"}
    argumentos = dict(argumentos or {})
    permitidos = set(HERRAMIENTAS[nombre]["parametros"])
    extra = set(argumentos) - permitidos
    if extra:
        return {
            "error": f"Argumentos no validos para {nombre}: {sorted(extra)}. "
            f"Permitidos: {sorted(permitidos)}"
        }
    faltan = [r for r in HERRAMIENTAS[nombre]["requeridos"] if r not in argumentos]
    if faltan:
        return {"error": f"Faltan argumentos para {nombre}: {faltan}"}
    try:
        return HERRAMIENTAS[nombre]["funcion"](ctx, **argumentos)
    except (ErrorHerramienta, TypeError) as e:
        return {"error": str(e)}

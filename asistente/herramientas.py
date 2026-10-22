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
  - Lo que el modelo manda pasa por normalizar() antes de llegar a una
    herramienta: una sola funcion para todas, que corrige lo que los
    modelos mandan mal (numeros y listas como texto, "None" para omitir,
    desde y ultimos_meses a la vez) y deja cada correccion en el log.

Sin Streamlit ni Spark: lo usan igual la pagina del asistente, las pruebas
y la evaluacion.
"""

import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from dashboard import datos
from src.simulacion import montecarlo as mc

RAIZ = Path(__file__).resolve().parents[1]

log = logging.getLogger(__name__)

VACIOS = {"", "None", "none", "null", "NULL"}
# Argumentos en los que 0 no es un valor sino la forma del modelo de decir
# "sin limite" (llama3.1:8b mandaba ultimos_meses 0 para "en que se me va el
# dinero").
CERO_ES_OMITIDO = {"ultimos_meses"}

PATRON_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
PATRON_NUMERO = re.compile(r"^-?\d+(\.\d+)?$")

# Categorias de salida de gold_movimientos_mensuales (las de "ingreso" solo
# tienen entradas).
CATEGORIAS = [
    "comida",
    "transporte",
    "entretenimiento",
    "servicios",
    "salud",
    "educacion",
    "hogar",
    "ropa",
    "suscripciones",
    "otros",
    "pago_deuda",
    "efectivo",
    "transferencia_personal",
]

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
# Compras que no son vivienda (evaluar_compra). Rangos amplios a proposito:
# el credito al consumo en Mexico puede pasar del 40 % anual.
PLAZO_COMPRA_MESES = (1, 120)
TASA_COMPRA = (0.0, 0.80)
ENGANCHE_COMPRA = (0.0, 0.90)
FORMAS_PAGO = ["contado", "credito"]


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


def _a_lista(nombre: str, valor) -> list:
    """Los modelos a veces mandan una lista como texto ("['a', 'b']") o un
    solo valor en vez de una lista."""
    if isinstance(valor, list):
        return valor
    if isinstance(valor, str):
        texto = valor.strip()
        if texto.startswith("["):
            try:
                lista = json.loads(texto.replace("'", '"'))
            except json.JSONDecodeError:
                raise ErrorHerramienta(
                    f"{nombre} debe ser una lista: {valor!r}"
                ) from None
            if isinstance(lista, list):
                return lista
        return [texto]
    raise ErrorHerramienta(f"{nombre} debe ser una lista: {valor!r}")


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


def _rango(
    ctx: Contexto,
    desde: str | None,
    hasta: str | None,
    ultimos_meses=None,
) -> tuple[str, str]:
    """Rango de meses validado. Con ultimos_meses, los n meses que terminan
    en hasta (o en el ultimo mes con datos): el modelo no tiene que hacer
    aritmetica de fechas, que es donde se equivoca. ultimos_meses manda sobre
    desde: el modelo suele llenar los dos y el que calcula mal es desde (con
    llama3.1:8b, "los ultimos 3 meses" llego como desde 2027-01, hasta
    2027-04, ultimos_meses 3). Sin limites, la ventana
    de las features (los ultimos ventana_meses meses hasta el corte), la
    misma de las metricas del cliente.

    Un mes mal escrito o un rango fuera de los datos es un error que dice
    que meses hay; un rango valido sin movimientos no lo es (la herramienta
    devuelve 0 y sin_movimientos)."""
    meses = _meses_disponibles(ctx)
    if not meses:
        raise ErrorHerramienta(f"{ctx.cliente_id} no tiene movimientos en Gold")
    disponibles = f"Hay movimientos de {meses[0]} a {meses[-1]}"
    for nombre, valor in (("desde", desde), ("hasta", hasta)):
        if valor is not None and not PATRON_MES.match(str(valor)):
            raise ErrorHerramienta(
                f"{nombre} debe tener formato AAAA-MM: {valor!r}. {disponibles}"
            )
    hasta = hasta or meses[-1]
    if ultimos_meses is not None:
        if ultimos_meses < 1:
            raise ErrorHerramienta(
                f"ultimos_meses debe ser un entero positivo: {ultimos_meses!r}"
            )
        desde = datos.inicio_ventana(hasta, ultimos_meses)
    desde = desde or datos.inicio_ventana(meses[-1], ctx.ventana_meses)
    if desde > hasta:
        raise ErrorHerramienta(
            f"desde ({desde}) es posterior a hasta ({hasta}). {disponibles}"
        )
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
    ultimos_meses: int | None = None,
) -> dict:
    """Salidas del cliente por categoria en un rango de meses. Con
    categoria, el detalle mes a mes de esa categoria. Una categoria que no
    existe es un error con las validas; una que existe pero no tuvo salidas
    en el rango da 0 y sin_movimientos: es una respuesta, no un error."""
    if categoria is not None and categoria not in CATEGORIAS:
        raise ErrorHerramienta(
            f"Categoria no valida: {categoria!r}. Categorias validas: {CATEGORIAS}"
        )
    desde, hasta = _rango(ctx, desde, hasta, ultimos_meses)
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
        resultado["sin_movimientos"] = df.empty
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
    # Con categoria, el total de la categoria va primero y el de todas las
    # salidas cambia de nombre: con "total_salidas" al lado, llama3.1:8b
    # contestaba "cuanto pague de deuda" con el total de todas las salidas.
    de_categoria = df[df["categoria"] == categoria].sort_values("mes")
    subtotal = float(de_categoria["total"].sum())
    proporcion = subtotal / total if total else None
    lectura = f"Salidas en {categoria} de {desde} a {hasta}: ${subtotal:,.2f}" + (
        f" ({proporcion * 100:.1f} % de todas las salidas del periodo)."
        if total
        else "."
    )
    return {
        "categoria": categoria,
        "desde": desde,
        "hasta": hasta,
        "total_categoria": _numero(subtotal),
        "lectura": lectura,
        "sin_movimientos": de_categoria.empty,
        "proporcion": _numero(proporcion, 4),
        "por_mes": [
            {"mes": m, "total": _numero(t)}
            for m, t in zip(de_categoria["mes"], de_categoria["total"])
        ],
        "total_todas_las_categorias": _numero(total),
    }


def flujo_mensual(
    ctx: Contexto,
    desde: str | None = None,
    hasta: str | None = None,
    ultimos_meses: int | None = None,
) -> dict:
    """Entradas, salidas y neto del cliente por mes en un rango."""
    desde, hasta = _rango(ctx, desde, hasta, ultimos_meses)
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
        "sin_movimientos": df.empty,
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
    comparacion = []
    for v in variables:
        proporcion = datos.percentil_en_portafolio(df, v, fila[v])
        valor, mediana = fila[v], df[v].median()
        comparacion.append(
            {
                "variable": v,
                "valor_cliente": _numero(valor, 4),
                "mediana_portafolio": _numero(mediana, 4),
                "proporcion_menor_o_igual": _numero(proporcion, 4),
                # La frase ya armada: con los numeros sueltos, llama3.1:8b
                # hacia sus propias cuentas (dividia valor entre mediana).
                "lectura": _lectura_comparacion(v, valor, mediana, proporcion),
            }
        )
        if v == "probabilidad_impago":
            # Sin la banda, el modelo llamaba "alto" a un 8 % por estar sobre
            # la mediana, aunque la banda configurada fuera "bajo".
            comparacion[-1]["lectura"] += (
                f" Banda de riesgo del cliente: {fila['banda_riesgo']} "
                f"({_limites_bandas(ctx.bandas)})."
            )
    return {"clientes_en_portafolio": len(df), "comparacion": comparacion}


def _limites_bandas(bandas: list) -> str:
    ordenadas = sorted(bandas, key=lambda b: b["desde"])
    return ", ".join(f"{b['nombre']} desde {b['desde'] * 100:.0f} %" for b in ordenadas)


def _lectura_comparacion(variable: str, valor, mediana, proporcion) -> str:
    if valor is None or pd.isna(valor) or pd.isna(proporcion):
        return f"{variable}: sin dato para este cliente."
    lado = "por encima de" if valor > mediana else "por debajo de"
    if valor == mediana:
        lado = "igual a"

    def cifra(x) -> str:
        return f"{x:,.2f}" if abs(x) >= 1 else f"{x:.4f}"

    return (
        f"{variable}: {cifra(valor)} ({lado} la mediana del portafolio, "
        f"{cifra(mediana)}); el {proporcion * 100:.1f} % de los clientes "
        "tiene un valor menor o igual."
    )


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
    if enganche_pct is None:
        enganche_pct = float(cfg["hipoteca"]["enganche_pct"])
    if plazo_anios is None:
        plazo_anios = int(cfg["hipoteca"]["plazo_anios"])
    if tasa_anual is None:
        tasa_anual = float(cfg["hipoteca"]["tasa_anual"])
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

    supuestos = _supuestos_simulacion(cfg)
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
        # Con los numeros sueltos, llama3.1:8b leyo 99.98 % de deficit como
        # "muy baja".
        "lectura": (
            f"Cuota de ${hipoteca.cuota_mensual:,.2f} al mes. Probabilidad de "
            f"quedar en saldo negativo en los proximos {supuestos.meses} meses: "
            f"{sin.probabilidad_deficit * 100:.1f} % sin la hipoteca y "
            f"{con.probabilidad_deficit * 100:.1f} % con ella."
            + _advertencia(con.probabilidad_deficit)
            + " "
            + (
                "Con su flujo actual no ahorra, asi que no reuniria el pago "
                f"inicial de ${hipoteca.pago_inicial:,.2f}."
                if meses_enganche is None
                else f"Reuniria el pago inicial de ${hipoteca.pago_inicial:,.2f} "
                f"en {meses_enganche:.1f} meses."
            )
        ),
    }


def _advertencia(probabilidad_con: float) -> str:
    """Frase fija cuando, con la compra, quedar en saldo negativo es mas
    probable que no: sin ella, llama3.1:8b contestaba "puedes comprarlo" con
    100 % de probabilidad de deficit."""
    if probabilidad_con >= 0.5:
        return (
            " Con la compra es mas probable quedar en saldo negativo que no: no "
            "es una compra que su flujo sostenga."
        )
    return ""


def _supuestos_simulacion(cfg: dict) -> mc.Supuestos:
    choque = cfg["choque_ingreso"]
    return mc.Supuestos(
        meses=int(cfg["simulacion"]["meses"]),
        trayectorias=int(cfg["simulacion"]["trayectorias"]),
        semilla=int(cfg["simulacion"]["semilla"]),
        prob_choque_mensual=float(choque["probabilidad_mensual"]),
        duracion_choque=tuple(int(x) for x in choque["duracion_meses"]),
    )


def evaluar_compra(
    ctx: Contexto,
    precio: float,
    forma_pago: str,
    plazo_meses: int | None = None,
    tasa_anual: float | None = None,
    enganche_pct: float | None = None,
) -> dict:
    """Cualquier compra que no sea vivienda (un auto, un viaje, un equipo),
    de contado o a credito, con el mismo Monte Carlo del simulador: el
    historial propio del cliente remuestreado, el choque de ingreso y la
    misma semilla, sobre las mismas muestras con y sin la compra.

    - Contado: el precio sale del saldo hoy. Si el saldo no alcanza, cuantos
      meses le tomaria reunirlo a su ritmo de ahorro.
    - Credito: cuota fija durante el plazo (solo los meses del plazo dentro
      del horizonte simulado). El enganche, si hay, se evalua aparte como en
      la hipoteca (meses para reunirlo).

    No inventa condiciones: un credito sin plazo o sin tasa es un error que
    dice que dato falta, para que el modelo se lo pregunte al usuario."""
    if forma_pago not in FORMAS_PAGO:
        raise ErrorHerramienta(
            f"forma_pago debe ser una de {FORMAS_PAGO}: {forma_pago!r}"
        )
    if not 0 < precio <= PRECIO_MAXIMO:
        raise ErrorHerramienta(f"precio debe estar entre 0 y {PRECIO_MAXIMO:,}")
    supuestos_texto = []
    if forma_pago == "credito":
        faltan = [
            n
            for n, v in (("plazo_meses", plazo_meses), ("tasa_anual", tasa_anual))
            if v is None
        ]
        if faltan:
            raise ErrorHerramienta(
                f"Para una compra a credito falta {' y '.join(faltan)}: "
                "preguntalo al usuario, no lo supongas"
            )
        if not PLAZO_COMPRA_MESES[0] <= plazo_meses <= PLAZO_COMPRA_MESES[1]:
            raise ErrorHerramienta(
                f"plazo_meses debe estar entre {PLAZO_COMPRA_MESES[0]} y "
                f"{PLAZO_COMPRA_MESES[1]}. Si es una vivienda, usa simular_hipoteca"
            )
        if not TASA_COMPRA[0] <= tasa_anual <= TASA_COMPRA[1]:
            raise ErrorHerramienta(
                f"tasa_anual debe estar entre {TASA_COMPRA[0]} y {TASA_COMPRA[1]} "
                "(fraccion del 0 al 1)"
            )
        if enganche_pct is None:
            enganche_pct = 0.0
            supuestos_texto.append("sin enganche (el usuario no lo dio)")
        if not ENGANCHE_COMPRA[0] <= enganche_pct <= ENGANCHE_COMPRA[1]:
            raise ErrorHerramienta(
                f"enganche_pct debe estar entre {ENGANCHE_COMPRA[0]} y "
                f"{ENGANCHE_COMPRA[1]}"
            )
    elif any(v is not None for v in (plazo_meses, tasa_anual, enganche_pct)):
        raise ErrorHerramienta(
            "plazo_meses, tasa_anual y enganche_pct solo aplican a forma_pago credito"
        )

    supuestos = _supuestos_simulacion(ctx.simulacion)
    try:
        historial = mc.historial_cliente(ctx.ruta_gold, ctx.cliente_id)
        saldo = mc.saldo_inicial_cliente(ctx.ruta_gold, ctx.cliente_id)
    except ValueError as e:
        raise ErrorHerramienta(str(e)) from e
    muestras = mc.muestrear(len(historial.meses), supuestos)
    sin_compra = mc.simular(historial, saldo, muestras)
    sin = mc.resumir(sin_compra)
    resultado = {
        "forma_pago": forma_pago,
        "precio": _numero(precio),
        "saldo_disponible": _numero(saldo),
        "ahorro_mensual_promedio": _numero(historial.neto.mean()),
        "meses_simulados": supuestos.meses,
        "probabilidad_deficit_sin_compra": _numero(sin.probabilidad_deficit, 4),
    }

    if forma_pago == "contado":
        con = mc.resumir(sin_compra - precio)
        alcanza = saldo >= precio
        meses = mc.meses_para_juntar(precio, historial, saldo)
        resultado.update(
            {
                "alcanza_con_el_saldo_hoy": alcanza,
                "probabilidad_deficit_con_compra": _numero(con.probabilidad_deficit, 4),
                "meses_para_reunir_el_precio": _numero(meses, 1),
            }
        )
        if alcanza:
            lectura = (
                f"De contado: el saldo (${saldo:,.2f}) alcanza para ${precio:,.2f}. "
                "Probabilidad de quedar en saldo negativo en los proximos "
                f"{supuestos.meses} meses: {sin.probabilidad_deficit * 100:.1f} % sin "
                f"la compra y {con.probabilidad_deficit * 100:.1f} % con ella."
                + _advertencia(con.probabilidad_deficit)
            )
        elif meses is None:
            lectura = (
                f"De contado: el saldo (${saldo:,.2f}) no alcanza para "
                f"${precio:,.2f} y con su flujo actual no ahorra, asi que no lo "
                "reuniria."
            )
        else:
            lectura = (
                f"De contado: el saldo (${saldo:,.2f}) no alcanza para "
                f"${precio:,.2f}; a su ritmo de ahorro lo reuniria en {meses:.1f} meses."
            )
    else:
        monto = precio * (1 - enganche_pct)
        cuota = mc.cuota_fija(monto, tasa_anual, int(plazo_meses))
        # La cuota solo se paga durante el plazo: meses acumulados de pago
        # hasta cada cierre de mes del horizonte.
        pagados = np.minimum(np.arange(supuestos.meses + 1), int(plazo_meses))
        con = mc.resumir(sin_compra - cuota * pagados)
        enganche = precio * enganche_pct
        meses_enganche = (
            mc.meses_para_juntar(enganche, historial, saldo) if enganche else 0.0
        )
        resultado.update(
            {
                "plazo_meses": int(plazo_meses),
                "tasa_anual": _numero(tasa_anual, 4),
                "enganche_pct": _numero(enganche_pct, 4),
                "monto_credito": _numero(monto),
                "cuota_mensual": _numero(cuota),
                "probabilidad_deficit_con_compra": _numero(con.probabilidad_deficit, 4),
                "meses_para_reunir_enganche": _numero(meses_enganche, 1),
            }
        )
        if plazo_meses > supuestos.meses:
            supuestos_texto.append(
                f"se simulan los primeros {supuestos.meses} de {int(plazo_meses)} "
                "meses del credito"
            )
        lectura = (
            f"A credito: cuota de ${cuota:,.2f} al mes durante {int(plazo_meses)} "
            f"meses. Probabilidad de quedar en saldo negativo en los proximos "
            f"{supuestos.meses} meses: {sin.probabilidad_deficit * 100:.1f} % sin la "
            f"compra y {con.probabilidad_deficit * 100:.1f} % con ella."
            + _advertencia(con.probabilidad_deficit)
        )
    resultado["supuestos"] = supuestos_texto
    resultado["lectura"] = lectura
    return resultado


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
_ULTIMOS = {
    "type": "integer",
    "description": "Numero de meses que terminan en hasta (o en el ultimo con "
    "datos), para preguntas como 'los ultimos 3 meses'. Si se da, manda sobre desde",
}
_SI_LO_DIJO = " Omitelo si el usuario no lo dijo: se usa el valor de la configuracion."

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
        "descripcion": "Salidas del cliente (en que gasta) por categoria en un "
        "rango de meses; sin rango, los ultimos 12. Sin categoria devuelve el "
        "desglose de todas, que es lo que responde 'en que se me va el dinero'.",
        "parametros": {
            "desde": _MES,
            "hasta": _MES,
            "ultimos_meses": _ULTIMOS,
            "categoria": {
                "type": "string",
                "enum": CATEGORIAS,
                "description": "Solo si pregunta por una categoria concreta; "
                "entonces devuelve su detalle mes a mes",
            },
        },
        "requeridos": [],
    },
    "flujo_mensual": {
        "funcion": flujo_mensual,
        "descripcion": "Entradas, salidas y neto del cliente mes a mes en un "
        "rango de meses (por defecto, los ultimos 12).",
        "parametros": {"desde": _MES, "hasta": _MES, "ultimos_meses": _ULTIMOS},
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
        "descripcion": "Solo para comprar vivienda: simula contratar una "
        "hipoteca (riesgo de quedar en saldo negativo con y sin ella, cuota "
        "mensual, capacidad hipotecaria y meses para reunir el enganche). Para "
        "cualquier otra compra usa evaluar_compra.",
        "parametros": {
            "precio": {"type": "number", "description": "Precio de la vivienda (MXN)"},
            "enganche_pct": {
                "type": "number",
                "description": "Fraccion del precio, entre 0.05 y 0.40." + _SI_LO_DIJO,
            },
            "plazo_anios": {
                "type": "integer",
                "enum": PLAZOS_ANIOS,
                "description": "Plazo del credito en anios." + _SI_LO_DIJO,
            },
            "tasa_anual": {
                "type": "number",
                "description": "Tasa anual como fraccion, entre 0.06 y 0.18."
                + _SI_LO_DIJO,
            },
            "renta_reemplazada": {
                "type": "number",
                "description": "Renta mensual que deja de pagar (MXN)." + _SI_LO_DIJO,
            },
        },
        "requeridos": ["precio"],
    },
    "evaluar_compra": {
        "funcion": evaluar_compra,
        "descripcion": "Compras que NO son vivienda (auto, viaje, equipo...), "
        "de contado o a credito. Una casa, departamento o terreno va siempre con "
        "simular_hipoteca. Devuelve  si el saldo alcanza, cuota, riesgo "
        "de quedar en saldo negativo con y sin la compra, y meses para reunir el "
        "precio o el enganche. Necesita el precio que dio el usuario; a credito "
        "tambien plazo y tasa. Si el usuario no los dio, preguntalos en vez de "
        "llamar esta herramienta.",
        "parametros": {
            "precio": {
                "type": "number",
                "description": "Precio (MXN) que dio el usuario",
            },
            "forma_pago": {"type": "string", "enum": FORMAS_PAGO},
            "plazo_meses": {
                "type": "integer",
                "description": "Plazo del credito en meses, solo si el usuario lo dio",
            },
            "tasa_anual": {
                "type": "number",
                "description": "Tasa anual del credito como fraccion, solo si el "
                "usuario la dio",
            },
            "enganche_pct": {
                "type": "number",
                "description": "Fraccion del precio que paga al inicio, solo si el "
                "usuario la dio",
            },
        },
        "requeridos": ["precio", "forma_pago"],
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


@dataclass(frozen=True)
class Coercion:
    """Una correccion aplicada a un argumento del modelo. final None quiere
    decir que el argumento se descarto."""

    herramienta: str
    argumento: str
    original: object
    final: object
    regla: str


def _para_log(valor, esquema_argumento: dict):
    """Lo que el log puede escribir de un valor: numeros (tambien como
    texto, "3"), meses AAAA-MM,
    valores del catalogo del esquema (enum) y los marcadores de vacio, tal
    cual. Cualquier otro texto se reemplaza por su largo: es la garantia de
    que el log no copia texto libre aunque una herramienta llegue a
    recibirlo."""
    permitidos = set(esquema_argumento.get("enum") or [])
    permitidos |= set(esquema_argumento.get("items", {}).get("enum") or [])

    def seguro(v) -> bool:
        if v is None or isinstance(v, bool | int | float):
            return True
        if isinstance(v, str):
            return (
                v.strip() in VACIOS
                or bool(PATRON_MES.match(v))
                or bool(PATRON_NUMERO.match(v.strip()))
                or v in permitidos
            )
        return False

    if isinstance(valor, list):
        return (
            valor
            if all(seguro(v) for v in valor)
            else f"<lista omitida, {len(valor)} elementos>"
        )
    if seguro(valor):
        return valor
    return f"<texto omitido, {len(str(valor))} caracteres>"


def _convertir(argumento: str, valor, tipo: str | None):
    """Valor con el tipo del esquema. Un texto ("string") se deja igual."""
    if tipo == "number":
        return _a_numero(argumento, valor)
    if tipo == "integer":
        numero = _a_numero(argumento, valor)
        if numero != int(numero):
            raise ErrorHerramienta(
                f"{argumento} debe ser un entero positivo: {valor!r}"
            )
        return int(numero)
    if tipo == "array":
        return _a_lista(argumento, valor)
    return valor


def normalizar(nombre: str, argumentos: dict | None) -> tuple[dict, list[Coercion]]:
    """Unico punto donde se corrigen los argumentos del modelo, para todas las
    herramientas. Reglas, en orden:

      1. "None", "null", "" (o None) cuentan como argumento omitido, y 0 en
         ultimos_meses tambien.
      2. Cada valor toma el tipo de su esquema: numeros y enteros que llegan
         como texto, y listas como texto ("['a']") o como un solo valor.
      3. ultimos_meses manda sobre desde: si llegan los dos, desde se
         descarta (ver _rango).

    Cada correccion queda en el log con la herramienta, el argumento, el
    valor original y el final. Un valor que no se puede convertir lanza
    ErrorHerramienta.

    Que llega al log: solo argumentos del esquema, y de ellos solo los que
    se corrigen. Un argumento fuera del esquema no se registra nunca (pasa
    sin tocar y ejecutar() lo rechaza). Los valores pasan por _para_log():
    numeros, meses y valores del catalogo se escriben tal cual y cualquier
    otro texto se reemplaza por su largo, asi que si una herramienta llega a
    recibir texto libre del usuario, el log no lo copia."""
    esquema = HERRAMIENTAS[nombre]["parametros"]
    limpios, coerciones = {}, []
    for argumento, valor in (argumentos or {}).items():
        vacio = valor is None or (isinstance(valor, str) and valor.strip() in VACIOS)
        if argumento not in esquema:
            # Sin registro: puede ser cualquier cosa que invente el modelo.
            if not vacio:
                limpios[argumento] = valor
            continue
        if vacio:
            coerciones.append(
                Coercion(nombre, argumento, valor, None, "vacio_como_omitido")
            )
            continue
        final = _convertir(argumento, valor, esquema[argumento].get("type"))
        if argumento in CERO_ES_OMITIDO and final == 0:
            coerciones.append(
                Coercion(nombre, argumento, valor, None, "cero_como_omitido")
            )
            continue
        if type(final) is not type(valor) or final != valor:
            coerciones.append(Coercion(nombre, argumento, valor, final, "tipo"))
        limpios[argumento] = final
    if "ultimos_meses" in limpios and "desde" in limpios:
        coerciones.append(
            Coercion(nombre, "desde", limpios.pop("desde"), None, "ultimos_meses_manda")
        )
    for c in coerciones:
        log.info(
            "coercion herramienta=%s argumento=%s original=%r final=%r regla=%s",
            c.herramienta,
            c.argumento,
            _para_log(c.original, esquema[c.argumento]),
            _para_log(c.final, esquema[c.argumento]),
            c.regla,
        )
    return limpios, coerciones


def ejecutar(nombre: str, argumentos: dict | None, ctx: Contexto) -> dict:
    """Ejecuta la herramienta que pidio el modelo. Un nombre o argumento
    que no esta en el esquema, o cualquier ErrorHerramienta, vuelve como
    {"error": ...}. cliente_id siempre sale del contexto."""
    if nombre not in HERRAMIENTAS:
        return {"error": f"No existe la herramienta {nombre!r}"}
    try:
        argumentos, _ = normalizar(nombre, argumentos)
    except ErrorHerramienta as e:
        return {"error": str(e)}
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

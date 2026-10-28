"""
src/simulacion/montecarlo.py

Simulador Monte Carlo de escenarios financieros de un cliente. Primer
escenario: contratar una hipoteca.

Pregunta que responde: si este cliente contrata una hipoteca con estas
condiciones, que tan probable es que en los proximos meses su saldo no
alcance (quede negativo)? Y cual es el precio maximo de vivienda que puede
asumir sin pasar de un riesgo aceptable?

Como lo responde:

  1. Historial propio del cliente (gold_movimientos_mensuales): por cada
     mes observado, su ingreso laboral (nomina y depositos), otras
     entradas (P2P recibido) y todas sus salidas, incluidas las deudas que
     ya paga.
  2. Cada trayectoria simulada arma su futuro mes a mes eligiendo al azar
     meses de ese historial (bootstrap). Asi no se supone ninguna
     distribucion: un cliente informal con ingresos irregulares produce
     trayectorias irregulares, y uno formal, estables.
  3. Encima se sortea un choque que el historial no contiene: perder el
     ingreso laboral durante algunos meses.
  4. Saldo del mes = saldo anterior + entradas - salidas - cuota. Se repite
     miles de veces y se resume en probabilidades y bandas.

El enganche se evalua aparte (cubre_pago_inicial, meses_para_juntar): con
los saldos de la cuenta digital, casi ningun cliente podria pagarlo hoy, y
descontarlo del saldo haria que toda simulacion empiece en negativo. Asi
el reporte separa dos preguntas distintas: puede reunir el enganche, y
puede sostener la cuota una vez contratada.

Limitaciones que conviene decir en voz alta: el bootstrap supone meses
independientes (pierde rachas y estacionalidad), el horizonte es corto a
proposito (36 meses por default, no los 20 anos del credito), y el saldo
inicial es solo la cuenta digital (no cuenta CETES ni otros activos).

Modulo puro: solo NumPy, pandas y DuckDB. No usa Spark ni Streamlit, para
que lo usen igual el dashboard, la linea de comandos y las pruebas.
"""

from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd

from src.common.formato import formato_mxn

TIPOS_INGRESO_LABORAL = ("nomina", "deposito")


# ---------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Historial:
    """Flujos mensuales observados de un cliente, todos en positivo."""

    meses: tuple[str, ...]
    ingreso_laboral: np.ndarray
    otras_entradas: np.ndarray
    salidas: np.ndarray

    @property
    def neto(self) -> np.ndarray:
        return self.ingreso_laboral + self.otras_entradas - self.salidas

    @property
    def ingreso_medio(self) -> float:
        return float(self.ingreso_laboral.mean())


@dataclass(frozen=True)
class Hipoteca:
    precio: float
    tasa_anual: float
    plazo_anios: int
    enganche_pct: float
    gastos_iniciales_pct: float
    # Gasto mensual que la vivienda propia elimina (renta). El historial no
    # distingue renta de otros gastos, asi que lo indica quien simula; con 0
    # se supone que el cliente no renta, el caso mas conservador.
    renta_reemplazada: float = 0.0

    @property
    def monto_credito(self) -> float:
        return self.precio * (1 - self.enganche_pct)

    @property
    def pago_inicial(self) -> float:
        """Lo que sale del saldo el dia de la firma: enganche mas gastos."""
        return self.precio * (self.enganche_pct + self.gastos_iniciales_pct)

    @property
    def cuota_mensual(self) -> float:
        return cuota_fija(self.monto_credito, self.tasa_anual, self.plazo_anios * 12)

    @property
    def cargo_neto_mensual(self) -> float:
        """Lo que la hipoteca cambia en el flujo del mes: cuota menos la
        renta que deja de pagar."""
        return self.cuota_mensual - self.renta_reemplazada


@dataclass(frozen=True)
class Supuestos:
    meses: int = 36
    trayectorias: int = 5000
    semilla: int = 42
    prob_choque_mensual: float = 0.01
    duracion_choque: tuple[int, int] = (2, 6)


def cuota_fija(monto: float, tasa_anual: float, n_meses: int) -> float:
    """Pago mensual de un credito a tasa fija (amortizacion francesa)."""
    if monto <= 0:
        return 0.0
    tasa = tasa_anual / 12
    if tasa == 0:
        return monto / n_meses
    return monto * tasa / (1 - (1 + tasa) ** -n_meses)


def historial_cliente(ruta_duckdb: str, cliente_id: str) -> Historial:
    """Lee el historial mensual del cliente desde Gold. Los meses en que el
    cliente no tuvo movimientos de algun tipo valen 0 (rejilla completa de
    meses del periodo), igual que en los KPIs."""
    con = duckdb.connect(ruta_duckdb, read_only=True)
    try:
        df = con.execute(
            f"""
            WITH meses AS (SELECT DISTINCT mes FROM gold_movimientos_mensuales),
            cliente AS (
                SELECT
                    mes,
                    SUM(CASE WHEN tipo_transaccion IN {TIPOS_INGRESO_LABORAL}
                             THEN monto_total ELSE 0 END) AS ingreso_laboral,
                    SUM(CASE WHEN monto_total > 0
                              AND tipo_transaccion NOT IN {TIPOS_INGRESO_LABORAL}
                             THEN monto_total ELSE 0 END) AS otras_entradas,
                    -SUM(CASE WHEN monto_total < 0 THEN monto_total ELSE 0 END) AS salidas
                FROM gold_movimientos_mensuales
                WHERE cliente_id = ?
                GROUP BY mes
            )
            SELECT m.mes,
                   COALESCE(c.ingreso_laboral, 0) AS ingreso_laboral,
                   COALESCE(c.otras_entradas, 0) AS otras_entradas,
                   COALESCE(c.salidas, 0) AS salidas
            FROM meses m LEFT JOIN cliente c USING (mes)
            ORDER BY m.mes
            """,
            [cliente_id],
        ).fetchdf()
    finally:
        con.close()
    if (
        df.empty
        or (df[["ingreso_laboral", "otras_entradas", "salidas"]] == 0).all().all()
    ):
        raise ValueError(
            f"{cliente_id} no tiene movimientos en gold_movimientos_mensuales"
        )
    return Historial(
        meses=tuple(df["mes"]),
        ingreso_laboral=df["ingreso_laboral"].to_numpy(dtype=float),
        otras_entradas=df["otras_entradas"].to_numpy(dtype=float),
        salidas=df["salidas"].to_numpy(dtype=float),
    )


def saldo_inicial_cliente(ruta_duckdb: str, cliente_id: str) -> float:
    con = duckdb.connect(ruta_duckdb, read_only=True)
    try:
        fila = con.execute(
            "SELECT saldo_liquido_disponible FROM gold_features_cliente WHERE cliente_id = ?",
            [cliente_id],
        ).fetchone()
    finally:
        con.close()
    if fila is None:
        raise ValueError(f"{cliente_id} no esta en gold_features_cliente")
    return float(fila[0] or 0.0)


# ---------------------------------------------------------------------------
# Simulacion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Muestras:
    """El azar de una simulacion, sorteado una sola vez.

    Separarlo de la simulacion permite evaluar varios escenarios (sin
    hipoteca, con hipoteca, precios distintos) sobre exactamente los mismos
    meses y choques. Es la tecnica de numeros aleatorios comunes: las
    diferencias entre escenarios se deben al escenario y no al ruido de
    haber sorteado otra vez, y la busqueda de capacidad maxima es monotona.
    """

    indices_mes: np.ndarray  # (trayectorias, meses): que mes historico se usa
    en_choque: np.ndarray  # (trayectorias, meses): sin ingreso laboral ese mes


def muestrear(n_meses_historial: int, supuestos: Supuestos) -> Muestras:
    rng = np.random.default_rng(supuestos.semilla)
    forma = (supuestos.trayectorias, supuestos.meses)
    indices = rng.integers(0, n_meses_historial, size=forma)

    en_choque = np.zeros(forma, dtype=bool)
    restantes = np.zeros(supuestos.trayectorias, dtype=int)
    minimo, maximo = supuestos.duracion_choque
    for t in range(supuestos.meses):
        inicia = (restantes == 0) & (
            rng.random(supuestos.trayectorias) < supuestos.prob_choque_mensual
        )
        restantes[inicia] = rng.integers(minimo, maximo + 1, size=int(inicia.sum()))
        en_choque[:, t] = restantes > 0
        restantes = np.maximum(restantes - 1, 0)
    return Muestras(indices_mes=indices, en_choque=en_choque)


def simular(
    historial: Historial,
    saldo_inicial: float,
    muestras: Muestras,
    cuota_mensual: float = 0.0,
    pago_inicial: float = 0.0,
) -> np.ndarray:
    """Saldo de cada trayectoria al cierre de cada mes. Devuelve una matriz
    (trayectorias, meses + 1); la columna 0 es el saldo despues del pago
    inicial (enganche y gastos)."""
    idx = muestras.indices_mes
    ingreso = np.where(muestras.en_choque, 0.0, historial.ingreso_laboral[idx])
    neto = (
        ingreso + historial.otras_entradas[idx] - historial.salidas[idx] - cuota_mensual
    )
    trayectorias = idx.shape[0]
    inicio = np.full((trayectorias, 1), saldo_inicial - pago_inicial)
    return np.hstack([inicio, inicio + np.cumsum(neto, axis=1)])


@dataclass
class Resultado:
    probabilidad_deficit: float
    mes_mediano_primer_deficit: float | None
    saldo_final_p50: float
    bandas: pd.DataFrame = field(repr=False)


def resumir(saldos: np.ndarray) -> Resultado:
    """Probabilidad de quedar en saldo negativo en algun mes del horizonte,
    cuando ocurre (mediana entre las trayectorias que caen) y bandas de
    percentiles del saldo por mes."""
    negativo = saldos < 0
    alguna_vez = negativo.any(axis=1)
    primer_mes = negativo.argmax(axis=1)[alguna_vez]
    p10, p50, p90 = np.percentile(saldos, [10, 50, 90], axis=0)
    bandas = pd.DataFrame(
        {"mes": np.arange(saldos.shape[1]), "p10": p10, "p50": p50, "p90": p90}
    )
    return Resultado(
        probabilidad_deficit=float(alguna_vez.mean()),
        mes_mediano_primer_deficit=(
            float(np.median(primer_mes)) if primer_mes.size else None
        ),
        saldo_final_p50=float(p50[-1]),
        bandas=bandas,
    )


def evaluar_hipoteca(
    historial: Historial, saldo_inicial: float, hipoteca: Hipoteca, muestras: Muestras
) -> Resultado:
    """Sostenibilidad de la cuota: el enganche se supone cubierto con otros
    ahorros (ver meses_para_juntar), asi que no se descuenta del saldo."""
    saldos = simular(
        historial, saldo_inicial, muestras, cuota_mensual=hipoteca.cargo_neto_mensual
    )
    return resumir(saldos)


def meses_para_juntar(
    monto: float, historial: Historial, saldo_actual: float
) -> float | None:
    """Meses que le tomaria al cliente reunir `monto` a su ritmo de ahorro
    historico (neto mensual promedio), partiendo de su saldo actual. None si
    su neto promedio no es positivo: a ese ritmo no lo junta nunca."""
    faltante = monto - saldo_actual
    if faltante <= 0:
        return 0.0
    ahorro_mensual = float(historial.neto.mean())
    if ahorro_mensual <= 0:
        return None
    return faltante / ahorro_mensual


RIESGO_SATURADO = "riesgo_saturado"
AHORRO_NEGATIVO = "ahorro_negativo"
NO_ALCANZA_MINIMO = "no_alcanza_minimo"
SUPERA_TOPE = "supera_tope"
MOTIVOS_NO_APLICA = (RIESGO_SATURADO, AHORRO_NEGATIVO, NO_ALCANZA_MINIMO, SUPERA_TOPE)


@dataclass(frozen=True)
class Capacidad:
    """Capacidad hipotecaria: un monto, o "no aplica" con su motivo. Nunca
    un monto fijo en lugar del motivo: antes, el tope de la busqueda
    (20,000,000) se devolvia como si fuera la capacidad."""

    monto: float | None
    motivo: str | None = None
    detalle: str = ""

    @property
    def aplica(self) -> bool:
        return self.monto is not None


def capacidad_maxima(
    historial: Historial,
    saldo_inicial: float,
    base: Hipoteca,
    muestras: Muestras,
    incremento_maximo: float,
    precio_tope: float = 20_000_000,
    tolerancia: float = 10_000,
    precio_minimo: float = 0.0,
) -> Capacidad:
    """Precio maximo de vivienda (con las demas condiciones de `base`) con el
    que la hipoteca no sube la probabilidad de deficit mas de
    `incremento_maximo` sobre la del cliente sin hipoteca.

    El criterio es incremental a proposito: con el choque de ingreso,
    muchos clientes ya tienen cierta probabilidad de deficit sin deber
    nada, y un umbral absoluto los dejaria a todos sin capacidad. Lo que se
    le atribuye a la hipoteca es el riesgo que agrega.

    Busqueda binaria: con las mismas muestras, la probabilidad solo puede
    subir cuando sube el precio, asi que la frontera es unica.

    No aplica en cuatro casos, y se dice cual:
      - riesgo_saturado: sin hipoteca la probabilidad de deficit ya es tan
        alta que sumarle el incremento llega a 100 %; ningun precio rompe el
        criterio y la busqueda subia hasta el tope. Pasaba con todos los
        clientes con 95 % o mas de riesgo sin hipoteca (136 de 500 en la
        instancia del 2026-10-21).
      - ahorro_negativo: en promedio gasta mas de lo que le entra. Cerca de
        la saturacion el criterio incremental pierde sentido (pasar de 92 %
        a 97 % cuenta como 5 puntos) y estos clientes salian con capacidad.
      - no_alcanza_minimo: ni la vivienda de `precio_minimo` (la social mas
        barata, config/mercado_mx.yaml) cumple el criterio. Antes salian
        montos como 19,531, el primer paso de la busqueda.
      - supera_tope: ni el precio tope sube el riesgo mas del incremento; la
        frontera esta fuera del rango de busqueda."""
    riesgo_base = resumir(
        simular(historial, saldo_inicial, muestras)
    ).probabilidad_deficit
    limite = riesgo_base + incremento_maximo
    if limite >= 1:
        return Capacidad(
            None,
            RIESGO_SATURADO,
            f"Sin la hipoteca ya queda en saldo negativo en el "
            f"{riesgo_base * 100:.0f} % de los escenarios: ninguna hipoteca puede "
            f"subir ese riesgo menos de {incremento_maximo * 100:.0f} puntos sin "
            "llegar al 100 %, así que no hay una capacidad que calcular.",
        )
    ahorro_medio = float(historial.neto.mean())
    if ahorro_medio <= 0:
        return Capacidad(
            None,
            AHORRO_NEGATIVO,
            "En promedio gasta más de lo que le entra "
            f"({formato_mxn(-ahorro_medio, estilo='cliente')} al mes): cualquier "
            "cuota agranda ese faltante, así que no hay una capacidad que calcular.",
        )

    def cumple(precio: float) -> bool:
        hipoteca = Hipoteca(
            precio=precio,
            tasa_anual=base.tasa_anual,
            plazo_anios=base.plazo_anios,
            enganche_pct=base.enganche_pct,
            gastos_iniciales_pct=base.gastos_iniciales_pct,
            renta_reemplazada=base.renta_reemplazada,
        )
        return (
            evaluar_hipoteca(
                historial, saldo_inicial, hipoteca, muestras
            ).probabilidad_deficit
            <= limite
        )

    if precio_minimo > 0 and not cumple(precio_minimo):
        return Capacidad(
            None,
            NO_ALCANZA_MINIMO,
            "Ni una vivienda social de "
            f"{formato_mxn(precio_minimo, estilo='cliente')} (el precio mínimo, "
            "según la Sociedad Hipotecaria Federal) cabe sin subir el riesgo más de "
            f"{incremento_maximo * 100:.0f} puntos.",
        )

    bajo, alto = float(precio_minimo), float(precio_tope)
    if cumple(alto):
        return Capacidad(
            None,
            SUPERA_TOPE,
            f"Ni una vivienda de {formato_mxn(precio_tope, estilo='cliente')} sube "
            "el riesgo más de "
            f"{incremento_maximo * 100:.0f} puntos: la capacidad está por encima del "
            "rango que se busca.",
        )
    while alto - bajo > tolerancia:
        medio = (bajo + alto) / 2
        if cumple(medio):
            bajo = medio
        else:
            alto = medio
    return Capacidad(bajo)

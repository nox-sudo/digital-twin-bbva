"""
dashboard/vista_cliente.py

Textos de la vista "Mi resumen": cada tarjeta arma su frase a partir de
cifras que ya calculo Python (Gold, la escalera financiera, el Monte Carlo
y los valores SHAP del cliente). Sin Streamlit, para probar las frases con
pytest; la pagina (dashboard/paginas/mi_resumen.py) solo las muestra.

Tono: segunda persona, frases cortas, sin terminos tecnicos (nada de
"percentil" o "p10"). Montos con formato_mxn en estilo cliente.
"""

import math

import pandas as pd

from src.common.formato import formato_mxn
from src.gold.explicacion import Explicacion
from src.simulacion.montecarlo import (
    AHORRO_NEGATIVO,
    NO_ALCANZA_MINIMO,
    SUPERA_TOPE,
)

# Palabras internas que pueden llegar al texto del cliente desde los
# modulos de calculo (src/gold/escalera.py): se cambian por lenguaje comun
# solo al mostrarlas.
# Las tasas del catalogo son las de los productos del banco simulado, no
# tasas de mercado (las de Banxico estan en config/mercado_mx.yaml): por eso
# se dicen "las tasas de tus productos" y no "de referencia".
LENGUAJE_CLIENTE = {
    "Ingreso variable: la deuda se compara con el mes de menor ingreso de "
    "los últimos meses (CONDUSEF).": "Como tu ingreso cambia de un mes a otro, "
    "comparamos tus deudas con tu mes de menor ingreso.",
    "las tasas del catálogo": "las tasas de tus productos",
    "tasas del catálogo": "las tasas de tus productos",
}


def lenguaje_cliente(texto: str) -> str:
    for interno, comun in LENGUAJE_CLIENTE.items():
        texto = texto.replace(interno, comun)
    return texto


def pesos(valor) -> str:
    return formato_mxn(valor, estilo="cliente")


def texto_meses(meses: float) -> str:
    """'1 mes', '3 meses', '2.7 meses' (sin '1.0 meses')."""
    redondeado = round(float(meses), 1)
    if redondeado == 1:
        return "1 mes"
    if redondeado.is_integer():
        return f"{int(redondeado)} meses"
    return f"{redondeado:.1f} meses"


def periodo_anterior(n: int) -> str:
    """'el mes anterior' o 'los 18 meses anteriores'."""
    return "el mes anterior" if n == 1 else f"los {n} meses anteriores"


def resumen_mes(flujo: pd.DataFrame, nombre_mes) -> dict | None:
    """Lo que entro y salio el ultimo mes con datos, y lo que queda en un mes
    normal (promedio de los meses anteriores)."""
    if flujo.empty:
        return None
    flujo = flujo.sort_values("mes")
    ultimo = flujo.iloc[-1]
    anteriores = flujo.iloc[:-1]
    promedio = float(anteriores["neto"].mean()) if len(anteriores) else None
    entradas, salidas, neto = (
        float(ultimo["entradas"]),
        float(ultimo["salidas"]),
        float(ultimo["neto"]),
    )
    # Cada cifra dice su periodo: este mes, y el promedio de los meses
    # anteriores (todo el historial menos este mes), para que no se confunda
    # con el promedio de 12 meses de la explicacion del riesgo.
    frase = (
        f"Este mes ({nombre_mes(ultimo['mes'])}) te entraron {pesos(entradas)} y "
        f"salieron {pesos(salidas)}: "
    )
    if neto >= 0:
        frase += f"te quedaron {pesos(neto)}."
    else:
        frase += f"gastaste {pesos(-neto)} más de lo que te entró."
    if promedio is not None:
        periodo = periodo_anterior(len(anteriores))
        if promedio >= 0:
            frase += (
                f" En promedio, en {periodo}, te quedaban {pesos(promedio)} al mes."
            )
        else:
            frase += (
                f" En promedio, en {periodo}, gastabas {pesos(-promedio)} más de lo "
                "que te entraba al mes."
            )
    return {
        "mes": ultimo["mes"],
        "entradas": entradas,
        "salidas": salidas,
        "neto": neto,
        "promedio_neto": promedio,
        "frase": frase,
    }


# Cambio del ingreso por quincena (ultimos meses contra los primeros de la
# ventana) a partir del cual la frase lo menciona.
CAMBIO_RELEVANTE = 0.10
MESES_TENDENCIA = 3


def resumen_quincenas(
    quincenas: pd.DataFrame, proporcion_depositos_min: float, nombre_mes=str
) -> dict:
    """Cuanto llega en cada quincena. Ingreso fijo (nomina) o variable
    (mayoria de depositos), con el mismo umbral que la escalera financiera.

    Con nomina se dice lo que llego el ultimo mes, no el promedio del ano: si
    el ingreso bajo, el promedio describiria un sueldo que ya no tiene. Si
    cambio mas de CAMBIO_RELEVANTE contra el inicio de la ventana (con nomina,
    el primer mes; con depositos, el promedio de los primeros
    MESES_TENDENCIA meses contra los ultimos), la frase lo dice."""
    total = float(quincenas["monto"].sum())
    if total <= 0:
        return {
            "variable": False,
            "frase": "En el último año no registramos ingresos en tu cuenta.",
        }
    variable = float(quincenas["depositos"].sum()) / total > proporcion_depositos_min
    meses = sorted(quincenas["mes"].unique())
    ultimo = quincenas[quincenas["mes"] == meses[-1]].set_index("quincena")["monto"]
    primera = float(quincenas.loc[quincenas["quincena"] == 1, "monto"].mean())
    segunda = float(quincenas.loc[quincenas["quincena"] == 2, "monto"].mean())
    minima = float(quincenas["monto"].min())
    sin_ingreso = int((quincenas["monto"] <= 0).sum())
    inicio = float(
        quincenas[quincenas["mes"].isin(meses[:MESES_TENDENCIA])]["monto"].mean()
    )
    reciente = float(
        quincenas[quincenas["mes"].isin(meses[-MESES_TENDENCIA:])]["monto"].mean()
    )
    cambio = (reciente - inicio) / inicio if inicio > 0 else 0.0

    if not variable:
        u1, u2 = float(ultimo.get(1, 0.0)), float(ultimo.get(2, 0.0))
        if round(u1) == round(u2):
            frase = f"Te llegan unos {pesos(u1)} cada quincena: el 15 y a fin de mes."
        else:
            frase = (
                f"En {nombre_mes(meses[-1])} te llegaron {pesos(u1)} la primera "
                f"quincena y {pesos(u2)} la segunda."
            )
        # Nomina: el primer mes contra el ultimo, los dos que nombra la frase.
        antes = float(quincenas[quincenas["mes"] == meses[0]]["monto"].mean())
        ahora = (u1 + u2) / 2
        cambio = (ahora - antes) / antes if antes > 0 else 0.0
        if len(meses) > 1 and abs(cambio) >= CAMBIO_RELEVANTE:
            frase += (
                f" En {nombre_mes(meses[0])} te llegaban unos {pesos(antes)}: "
                f"tu ingreso {'bajó' if cambio < 0 else 'subió'} "
                f"{abs(cambio) * 100:.0f} %."
            )
    else:
        frase = (
            "Tus ingresos cambian de una quincena a otra: en promedio "
            f"{pesos(primera)} la primera y {pesos(segunda)} la segunda."
        )
        if sin_ingreso:
            frase += (
                f" En {sin_ingreso} "
                f"{'quincena' if sin_ingreso == 1 else 'quincenas'} no te entró nada."
            )
        else:
            frase += f" La más baja fue de {pesos(minima)}."
        if len(meses) > MESES_TENDENCIA and abs(cambio) >= CAMBIO_RELEVANTE:
            frase += (
                f" En los últimos {MESES_TENDENCIA} meses promedias {pesos(reciente)} "
                f"por quincena, {abs(cambio) * 100:.0f} % "
                f"{'menos' if cambio < 0 else 'más'} que al inicio."
            )
    return {
        "variable": variable,
        "primera": primera,
        "segunda": segunda,
        "minima": minima,
        "cambio": cambio,
        "frase": frase,
    }


def resumen_gastos(
    salidas: pd.DataFrame,
    nombre_categoria,
    meses: int,
    n: int = 3,
    con_cifras: bool = True,
) -> str:
    """Las n categorias que mas se llevaron el dinero en los ultimos meses.
    Sin cifras cuando la grafica de al lado ya escribe el monto de cada
    barra."""
    if salidas.empty or salidas["total"].sum() <= 0:
        return f"No registramos gastos en los últimos {meses} meses."
    partes = [
        (
            f"{nombre_categoria(f.categoria).lower()} ({pesos(f.total)}, "
            f"{f.proporcion * 100:.0f} %)"
            if con_cifras
            else nombre_categoria(f.categoria).lower()
        )
        for f in salidas.head(n).itertuples()
    ]
    if len(partes) > 1:
        lista = ", ".join(partes[:-1]) + " y " + partes[-1]
    else:
        lista = partes[0]
    return f"En los últimos {meses} meses, lo que más se llevó tu dinero fue {lista}."


def comparacion_gasto(flujo: pd.DataFrame, ventana: int | None = None) -> dict | None:
    """Lo que salio el ultimo mes contra el promedio de los meses anteriores
    (el mismo de resumen_mes), con el periodo dicho."""
    if flujo.empty:
        return None
    flujo = flujo.sort_values("mes")
    salidas = float(flujo.iloc[-1]["salidas"])
    # Con ventana, el promedio es el de los `ventana` meses anteriores (la
    # ventana principal de Mi resumen), no todo el historial.
    anteriores = flujo.iloc[:-1]["salidas"]
    if ventana:
        anteriores = anteriores.iloc[-ventana:]
    if anteriores.empty or float(anteriores.mean()) <= 0:
        return {
            "salidas": salidas,
            "cambio": None,
            "frase": "Lo que salió de tu cuenta.",
        }
    promedio = float(anteriores.mean())
    cambio = (salidas - promedio) / promedio
    referencia = f"tu promedio de {periodo_anterior(len(anteriores))}"
    if abs(cambio) < 0.01:
        frase = f"Igual que {referencia}."
    else:
        frase = (
            f"{abs(cambio) * 100:.0f} % {'más' if cambio > 0 else 'menos'} "
            f"que {referencia}."
        )
    return {"salidas": salidas, "promedio": promedio, "cambio": cambio, "frase": frase}


def mayor_aumento(cambios: pd.DataFrame, nombre_categoria, meses: int) -> dict:
    """La categoria cuyo gasto mensual mas subio en los ultimos meses contra
    los anteriores."""
    if cambios.empty or float(cambios["cambio"].max()) <= 0:
        return {
            "categoria": None,
            "titulo": "Nada subió",
            "frase": f"Ningún gasto aumentó contra los {meses} meses anteriores.",
        }
    fila = cambios.iloc[0]
    return {
        "categoria": fila["categoria"],
        "titulo": nombre_categoria(fila["categoria"]),
        "frase": (
            f"{pesos(fila['cambio'])} más al mes que en los {meses} meses "
            f"anteriores (de {pesos(fila['anterior'])} a {pesos(fila['reciente'])})."
        ),
    }


NIVEL_BANDA = {"bajo": "Bajo", "medio": "Medio", "alto": "Alto"}


# Meses de autonomia por debajo de los cuales el siguiente paso habla
# primero del deficit.
AUTONOMIA_MINIMA = 3


def autonomia_meses(saldo, capacidad_ahorro) -> float | None:
    """Cuantos meses alcanza el saldo al ritmo del deficit promedio de la
    ventana (capacidad de ahorro negativa). None si no hay deficit."""
    if capacidad_ahorro is None or pd.isna(capacidad_ahorro) or capacidad_ahorro >= 0:
        return None
    if saldo is None or pd.isna(saldo) or saldo <= 0:
        return 0.0
    return float(saldo) / -float(capacidad_ahorro)


def texto_autonomia(meses: float) -> str:
    """'menos de 1 mes', '1 mes' o '4 meses' (meses completos)."""
    if meses < 1:
        return "menos de 1 mes"
    completos = int(meses)
    return "1 mes" if completos == 1 else f"{completos} meses"


def frase_autonomia(meses: float | None, ventana: int) -> str | None:
    if meses is None:
        return None
    return (
        f"A tu ritmo de los últimos {ventana} meses, tu saldo alcanza para "
        f"{texto_autonomia(meses)}."
    )


def meta_escalera(paso: str, m: dict, cfg: dict) -> dict:
    """Lo concreto del paso actual de la escalera, con los mismos umbrales y
    metricas que src/gold/escalera.py usa para decidir el paso:

      accion  lo que toca hacer, con la meta ("Baja tus pagos de deuda a
              $10,999 al mes o menos").
      hoy     donde esta hoy ("Hoy pagas $25,513 al mes").
      falta   cuanto falta para la meta, o None.
    """
    u = cfg["umbrales"]
    if paso == "pagos_deuda":
        tope = u["pagos_deuda_max"]["valor"]
        hoy = f"Hoy pagas {pesos(m['pago_comprometido'])} al mes de deudas"
        if m["razon_pagos_deuda"] is None:
            return {
                "accion": (
                    f"Que tus pagos de deuda no pasen del {tope * 100:.0f} % de lo que "
                    "ganas."
                ),
                "hoy": hoy + " y no registramos ingreso",
                "falta": None,
            }
        maximo = tope * m["ingreso_referencia"]
        return {
            "accion": f"Baja tus pagos de deuda a {pesos(maximo)} al mes o menos.",
            "hoy": hoy,
            "falta": f"Te faltan {pesos(m['pago_comprometido'] - maximo)} por bajar",
        }
    if paso == "uso_tarjeta":
        tope = u["uso_tarjeta_max"]["valor"]
        return {
            "accion": f"Baja el uso de tu tarjeta al {tope * 100:.0f} % de su límite.",
            "hoy": f"Hoy usas el {m['uso_tarjeta'] * 100:.0f} % de su límite",
            "falta": (
                f"Te faltan {(m['uso_tarjeta'] - tope) * 100:.0f} puntos para bajar "
                f"al {tope * 100:.0f} %"
            ),
        }
    if paso in ("fondo_minimo", "fondo_meta"):
        meta = u["fondo_emergencia"][
            "minimo_meses" if paso == "fondo_minimo" else "meta_meses"
        ]
        faltan = max(meta * m["gasto_mensual"] - m["saldo_liquido"], 0.0)
        return {
            "accion": f"Junta {pesos(faltan)} más para cubrir {meta} meses de gastos.",
            "hoy": f"Hoy tu saldo cubre {texto_meses(m['fondo_meses'] or 0.0)} de gastos",
            "falta": None,
        }
    if paso == "ahorro":
        minimo = u["ahorro_min"]["valor"]
        if m["tasa_ahorro"] is None:
            return {
                "accion": f"Ahorra al menos el {minimo * 100:.0f} % de tu ingreso.",
                "hoy": "No registramos ingreso para medir cuánto ahorras",
                "falta": None,
            }
        return {
            "accion": f"Ahorra al menos {pesos(minimo * m['ingreso_ahorro'])} al mes.",
            "hoy": f"Hoy ahorras el {m['tasa_ahorro'] * 100:.0f} % de tu ingreso",
            "falta": None,
        }
    return {
        "accion": "Ya puedes pensar en invertir.",
        "hoy": "Tus deudas, tu tarjeta, tu fondo de emergencia y tu ahorro están en orden",
        "falta": None,
    }


def siguiente_paso_compacto(
    banda: str | None,
    saldo,
    capacidad_ahorro,
    ventana: int,
    nombre_paso: str | None,
    accion_escalera: str | None,
    probabilidad_deficit: float | None,
    meses_simulados: int,
) -> dict:
    """ "Tu siguiente paso" en tres piezas, con reglas fijas (mismas entradas,
    mismo texto):

      titular  una frase: lo urgente si el riesgo es alto o el saldo alcanza
               para menos de AUTONOMIA_MINIMA meses; si no, la meta de la
               escalera. No repite la autonomia, que vive en la tarjeta de
               saldo.
      accion   lo concreto que toca hacer (el paso actual de la escalera).
      chips    hasta dos cifras cortas, cada una con su periodo.
    """
    autonomia = autonomia_meses(saldo, capacidad_ahorro)
    urgente = banda == "alto" or (
        autonomia is not None and autonomia < AUTONOMIA_MINIMA
    )
    if urgente and autonomia is not None:
        # La autonomia (cuantos meses alcanza el saldo) ya esta en la tarjeta
        # de saldo; aqui va la causa, y su cifra en el primer chip.
        titular = "Lo urgente: cada mes sale más dinero del que te entra."
    elif urgente:
        titular = "Tu riesgo de atrasarte en tus pagos es alto."
    elif nombre_paso:
        titular = f"Tu meta ahora: {nombre_paso[0].lower()}{nombre_paso[1:]}."
    else:
        titular = "Todavía no tenemos datos para sugerirte un paso."

    chips = []
    if capacidad_ahorro is not None and not pd.isna(capacidad_ahorro):
        if capacidad_ahorro < 0:
            chips.append(
                f"Gastas {pesos(-capacidad_ahorro)} más de lo que te entra al mes "
                f"(promedio de {ventana} meses)"
            )
        else:
            chips.append(
                f"Te quedan {pesos(capacidad_ahorro)} al mes (promedio de {ventana} "
                "meses)"
            )
    if probabilidad_deficit is not None:
        palabra = certeza(probabilidad_deficit)
        quedarte = f"Quedarte sin dinero en {meses_simulados} meses"
        chips.append(
            f"{quedarte}: {palabra.replace('es ', '')}"
            if palabra
            else f"{quedarte}: {round(probabilidad_deficit * 100)} de cada 100 escenarios"
        )
    return {"titular": titular, "accion": accion_escalera, "chips": chips[:2]}


TONO_BANDA = {
    "bajo": "Vas bien: tu riesgo de atrasarte en tus pagos es bajo.",
    "medio": "Ojo: tu riesgo de atrasarte en tus pagos es medio.",
    "alto": "Cuidado: tu riesgo de atrasarte en tus pagos es alto.",
}


# KPIs de flujo de Gold calculados sobre la ventana de config/features.yaml
# (12 meses): su frase en la explicacion del riesgo dice ese periodo, para
# que no se confunda con las cifras de este mes.
VARIABLES_VENTANA = {
    "ingreso_mensual_promedio",
    "estabilidad_ingreso",
    "gasto_mensual_total",
    "capacidad_ahorro",
    "frecuencia_transacciones",
    "actividad_p2p",
}


# Factores cuya cifra ya se muestra en otra parte de Mi resumen: aqui se
# dicen sin el monto, para que cada cifra viva en un solo lugar. El saldo no
# dice "bajo": el efecto del saldo en el modelo no es monotono y puede subir
# el riesgo de un cliente con saldo mayor que la mediana (CLI-000303 en la
# instancia de la demo), asi que la frase solo nombra el dato.
FRASE_SIN_CIFRA = {
    ("capacidad_ahorro", True): "Gastas más de lo que te entra cada mes",
    ("capacidad_ahorro", False): "Te queda dinero cada mes",
    ("saldo_liquido_disponible", True): "Tu saldo disponible",
    ("saldo_liquido_disponible", False): "Tu saldo disponible",
    ("uso_linea_credito", True): "Usas buena parte del límite de tu tarjeta",
    ("uso_linea_credito", False): "Usas poco del límite de tu tarjeta",
}


def _sin_valor(valor) -> bool:
    return valor is None or pd.isna(valor) or float(valor) == 0


def _frase_por_valor(factor) -> str | None:
    """Casos en que la frase con la cifra se lee mal: sin tarjeta, el uso del
    limite es 0 y no "poco"; una deuda menor a 0.05 veces el ingreso sale
    como "0.0 veces"."""
    if factor.variable == "uso_linea_credito" and _sin_valor(factor.valor):
        return "No usas tarjeta de crédito"
    if factor.variable == "ratio_endeudamiento":
        if _sin_valor(factor.valor):
            return "No tienes deudas"
        if float(factor.valor) < 0.05:
            return "Casi no tienes deudas"
    return None


def _con_periodo(factor, ventana: int | None, sin_cifra: bool = False) -> str:
    """Frase del factor SHAP. Con sin_cifra, los factores de FRASE_SIN_CIFRA
    van sin monto; los KPIs de flujo dicen su periodo."""
    especial = _frase_por_valor(factor)
    if especial:
        return especial
    if sin_cifra and factor.variable in {v for v, _ in FRASE_SIN_CIFRA}:
        return FRASE_SIN_CIFRA[(factor.variable, factor.sube)]
    if ventana and factor.variable in VARIABLES_VENTANA:
        return f"{factor.frase} (promedio de los últimos {ventana} meses)"
    return factor.frase


def resumen_riesgo(
    banda: str,
    explicacion: Explicacion | None,
    n: int,
    ventana: int | None = None,
    sin_cifra: bool = False,
) -> dict:
    """La banda en palabras y lo que mas mueve el score del cliente, con sus
    propios valores SHAP: lo que lo sube y lo que lo baja. Con ventana, las
    frases de los KPIs de flujo dicen su periodo."""
    frase = TONO_BANDA.get(banda, "Todavía no tenemos tu nivel de riesgo.")
    if explicacion is None:
        return {"frase": frase, "suben": [], "bajan": []}
    principales = explicacion.principales(n)
    return {
        "frase": frase,
        "suben": [_con_periodo(f, ventana, sin_cifra) for f in principales if f.sube],
        "bajan": [
            _con_periodo(f, ventana, sin_cifra) for f in principales if not f.sube
        ],
    }


def certeza(probabilidad: float) -> str | None:
    """Los extremos de la simulacion en palabras: 100 de cada 100 escenarios
    es "prácticamente seguro" y 0 de cada 100, "muy poco probable". Con 5,000
    escenarios, un conteo absoluto suena a certeza total, que el modelo no
    tiene. None en los demas casos (se dice el conteo). El numero exacto
    sigue en la vista del admin y en la documentacion."""
    p = round(float(probabilidad) * 100)
    if p >= 100:
        return "es prácticamente seguro"
    if p <= 0:
        return "es muy poco probable"
    return None


def frase_riesgo_compra(con: float, sin: float, meses: int) -> str:
    """Riesgo de quedarse sin dinero con y sin la compra, con los extremos
    en palabras (certeza())."""
    evento = f"te quedes sin dinero algún mes de los próximos {meses}"
    c, s = certeza(con), certeza(sin)
    if c and c == s:
        return f"Con o sin la compra, {c} que {evento}."
    if c:
        parte_con = f"Con la compra, {c} que {evento}"
    else:
        parte_con = (
            f"Con la compra, en {round(con * 100)} de cada 100 escenarios simulados "
            f"te quedarías sin dinero algún mes de los próximos {meses}"
        )
    parte_sin = f"sin ella, {s}" if s else f"sin ella, en {round(sin * 100)}"
    return f"{parte_con}; {parte_sin}."


# --- Simulador de hipoteca ---------------------------------------------------

# Paso del precio de la vivienda en el simulador (dashboard/paginas/simulador.py).
PASO_PRECIO = 50_000


def plazo_juntar(meses: float) -> str:
    """'1 mes', 'unos 8 meses' o 'unos 31 meses (2 años y medio)'."""
    n = math.ceil(meses)
    if n <= 1:
        return "1 mes"
    if n < 12:
        return f"unos {n} meses"
    medios = round(n / 6)
    anios, medio = divmod(medios, 2)
    texto = "1 año" if anios == 1 else f"{anios} años"
    if medio:
        texto += " y medio"
    return f"unos {n} meses ({texto})"


def veredicto_hipoteca(
    precio: float,
    cuota: float,
    ingreso_medio: float | None,
    capacidad: float | None,
    motivo: str | None,
    precio_minimo: float,
    ahorro_medio: float,
) -> dict:
    """Respuesta de arriba del simulador para el cliente: si la casa le
    alcanza, cuanto pagaria y que cambiar si no. "Alcanza" es el mismo
    criterio que la capacidad hipotecaria: que la hipoteca no suba demasiado
    los escenarios en que se queda sin dinero. probar es un precio redondo
    que si le alcanza, para el boton de la pagina (None si no hay)."""
    alcanza = motivo == SUPERA_TOPE or (capacidad is not None and precio <= capacidad)
    detalle = None
    if ingreso_medio:
        detalle = f"El {cuota / ingreso_medio * 100:.0f} % de lo que ganas al mes"
    probar = None
    if capacidad is not None:
        titulo = "Esta casa te alcanza" if alcanza else "Esta casa no te alcanza"
        if alcanza:
            sugerencia = (
                f"Con este enganche y plazo te alcanzaría una casa de hasta "
                f"{pesos(capacidad)}."
            )
        else:
            probar = max(PASO_PRECIO, math.floor(capacidad / PASO_PRECIO) * PASO_PRECIO)
            sugerencia = (
                f"Con este enganche y plazo te alcanza una casa de hasta "
                f"{pesos(capacidad)}. También ayuda dar más enganche o un plazo más "
                "largo."
            )
    elif motivo == SUPERA_TOPE:
        titulo = "Esta casa te alcanza"
        sugerencia = "Con lo que te queda cada mes, el precio no es lo que te limita."
    else:
        titulo = "Hoy no te alcanza una hipoteca"
        if motivo == AHORRO_NEGATIVO:
            sugerencia = (
                f"Hoy gastas más de lo que te entra (te faltan {pesos(-ahorro_medio)} "
                "al mes), y cualquier mensualidad agranda ese faltante."
            )
        elif motivo == NO_ALCANZA_MINIMO:
            sugerencia = (
                f"Ni la vivienda más barata, de {pesos(precio_minimo)}, te alcanza "
                "sin que subas mucho las veces que te quedarías sin dinero."
            )
        else:
            # La frase de riesgo de abajo ya dice que, aun sin hipoteca, se
            # quedaria sin dinero: aqui solo lo que conviene hacer.
            sugerencia = (
                "Antes de pedir una hipoteca, conviene que cada mes te sobre dinero."
            )
    return {
        "alcanza": alcanza,
        "titulo": titulo,
        "valor": f"{pesos(cuota)} al mes",
        "detalle": detalle,
        "sugerencia": sugerencia,
        "probar": probar,
    }


def frase_enganche(
    pago_inicial: float, meses: float | None, ahorro_medio: float
) -> str:
    """Lo que necesita antes de firmar y cuanto tarda en juntarlo."""
    frase = f"Antes de firmar necesitas {pesos(pago_inicial)} de enganche y gastos."
    if meses == 0:
        return frase + " Tu saldo de hoy ya los cubre."
    if meses is None:
        return (
            frase
            + " Hoy no te sobra dinero al mes, así que ahorrando no los juntarías."
        )
    return (
        frase
        + f" Ahorrando {pesos(ahorro_medio)} al mes, como hasta ahora, los juntas "
        f"en {plazo_juntar(meses)}. La simulación de abajo supone que ya los tienes."
    )


def resumen_compra(resultado: dict) -> str:
    """La respuesta de "¿me alcanza?" a partir de evaluar_compra."""
    if "error" in resultado:
        return resultado["error"]
    riesgo = frase_riesgo_compra(
        resultado["probabilidad_deficit_con_compra"],
        resultado["probabilidad_deficit_sin_compra"],
        resultado["meses_simulados"],
    )
    precio, saldo = resultado["precio"], resultado["saldo_disponible"]
    if resultado["forma_pago"] == "contado":
        if resultado["alcanza_con_el_saldo_hoy"]:
            return (
                f"Sí te alcanza: tienes {pesos(saldo)} y cuesta {pesos(precio)}. "
                + riesgo
            )
        meses = resultado["meses_para_reunir_el_precio"]
        frase = f"Hoy no te alcanza: te faltan {pesos(precio - saldo)}."
        if meses is None:
            return frase + " Con lo que te queda cada mes ahora, no lo juntarías."
        n = math.ceil(meses)
        plazo = "1 mes" if n <= 1 else f"unos {n} meses"
        return frase + f" Ahorrando como hasta ahora lo juntas en {plazo}."
    frase = (
        f"Pagarías {pesos(resultado['cuota_mensual'])} al mes durante "
        f"{resultado['plazo_meses']} meses, ya con el IVA de "
        f"{resultado['iva_sobre_intereses'] * 100:.0f} % sobre los intereses. "
    )
    return frase + riesgo

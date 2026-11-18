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

# Palabras internas que pueden llegar al texto del cliente desde los
# modulos de calculo (src/gold/escalera.py): se cambian por lenguaje comun
# solo al mostrarlas.
LENGUAJE_CLIENTE = {
    "las tasas del catálogo": "tasas de referencia",
    "tasas del catálogo": "tasas de referencia",
}


def lenguaje_cliente(texto: str) -> str:
    for interno, comun in LENGUAJE_CLIENTE.items():
        texto = texto.replace(interno, comun)
    return texto


def pesos(valor) -> str:
    return formato_mxn(valor, estilo="cliente")


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
    salidas: pd.DataFrame, nombre_categoria, meses: int, n: int = 3
) -> str:
    """Las n categorias que mas se llevaron el dinero en los ultimos meses."""
    if salidas.empty or salidas["total"].sum() <= 0:
        return f"No registramos gastos en los últimos {meses} meses."
    partes = [
        f"{nombre_categoria(f.categoria).lower()} ({pesos(f.total)}, "
        f"{f.proporcion * 100:.0f} %)"
        for f in salidas.head(n).itertuples()
    ]
    if len(partes) > 1:
        lista = ", ".join(partes[:-1]) + " y " + partes[-1]
    else:
        lista = partes[0]
    return f"En los últimos {meses} meses, lo que más se llevó tu dinero fue {lista}."


def comparacion_gasto(flujo: pd.DataFrame) -> dict | None:
    """Lo que salio el ultimo mes contra el promedio de los meses anteriores
    (el mismo de resumen_mes), con el periodo dicho."""
    if flujo.empty:
        return None
    flujo = flujo.sort_values("mes")
    salidas = float(flujo.iloc[-1]["salidas"])
    anteriores = flujo.iloc[:-1]["salidas"]
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


def siguiente_paso(
    nombre_paso: str | None,
    lectura: str | None,
    probabilidad_deficit: float | None,
    meses: int,
) -> str:
    """El siguiente paso del cliente, armado con reglas: el paso actual de la
    escalera financiera (lo que toca hacer y cuanto) y el riesgo de quedarse
    sin dinero que da el simulador con su flujo actual. Sin modelo de
    lenguaje."""
    partes = []
    if nombre_paso and lectura:
        # El nombre del paso es la meta ("Pagos de deuda bajo control"), no
        # el estado actual: dicho solo, se leeria como algo ya logrado.
        partes.append(
            f"Tu meta ahora: {nombre_paso[0].lower()}{nombre_paso[1:]}. {lectura}"
        )
    if probabilidad_deficit is not None:
        p = round(probabilidad_deficit * 100)
        if certeza(probabilidad_deficit):
            partes.append(
                f"Con tu flujo actual, {certeza(probabilidad_deficit)} que te quedes "
                f"sin dinero algún mes de los próximos {meses}."
            )
        else:
            partes.append(
                f"Con tu flujo actual, en {p} de cada 100 escenarios simulados te "
                f"quedarías sin dinero algún mes de los próximos {meses}."
            )
    return " ".join(partes) or "Todavía no tenemos datos para sugerirte un paso."


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


def siguiente_paso_priorizado(
    banda: str | None,
    probabilidad,
    saldo,
    capacidad_ahorro,
    ventana: int,
    nombre_paso: str | None,
    lectura: str | None,
    probabilidad_deficit: float | None,
    meses_simulados: int,
    factor_que_sube: str | None = None,
) -> list[str]:
    """Parrafos del siguiente paso, con reglas fijas (mismas entradas, mismo
    texto). Si el riesgo es alto, o hay deficit promedio y el saldo alcanza
    para menos de AUTONOMIA_MINIMA meses, primero va eso, con su cifra y su
    periodo, y el paso de la escalera queda debajo. Si no, el texto de
    siguiente_paso()."""
    autonomia = autonomia_meses(saldo, capacidad_ahorro)
    alto = banda == "alto"
    deficit_urgente = autonomia is not None and autonomia < AUTONOMIA_MINIMA
    if not (alto or deficit_urgente):
        return [
            siguiente_paso(nombre_paso, lectura, probabilidad_deficit, meses_simulados)
        ]

    riesgo = (
        f"tu riesgo de atrasarte en tus pagos es alto ({float(probabilidad) * 100:.1f} %)"
        if alto and probabilidad is not None and not pd.isna(probabilidad)
        else "tu riesgo de atrasarte en tus pagos es alto"
    )
    if autonomia is not None:
        primero = (
            f"Lo primero: en promedio, en los últimos {ventana} meses, gastas "
            f"{pesos(-capacidad_ahorro)} más de lo que te entra al mes. A ese ritmo, "
            f"tu saldo de {pesos(saldo)} alcanza para {texto_autonomia(autonomia)}."
        )
        if alto:
            primero += f" Además, {riesgo}."
    else:
        primero = f"Lo primero: {riesgo}."
        if factor_que_sube:
            primero += f" Lo que más lo sube: {factor_que_sube[0].lower()}{factor_que_sube[1:]}."
    parrafos = [primero]
    if nombre_paso and lectura:
        parrafos.append(
            f"Tu paso en la escalera: {nombre_paso[0].lower()}{nombre_paso[1:]}. "
            f"{lectura}"
        )
    simulacion = siguiente_paso(None, None, probabilidad_deficit, meses_simulados)
    if probabilidad_deficit is not None:
        parrafos.append(simulacion)
    return parrafos


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


def _con_periodo(factor, ventana: int | None) -> str:
    if ventana and factor.variable in VARIABLES_VENTANA:
        return f"{factor.frase} (promedio de los últimos {ventana} meses)"
    return factor.frase


def resumen_riesgo(
    banda: str, explicacion: Explicacion | None, n: int, ventana: int | None = None
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
        "suben": [_con_periodo(f, ventana) for f in principales if f.sube],
        "bajan": [_con_periodo(f, ventana) for f in principales if not f.sube],
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

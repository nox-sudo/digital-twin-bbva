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


def pesos(valor) -> str:
    return formato_mxn(valor, estilo="cliente")


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
    frase = (
        f"En {nombre_mes(ultimo['mes'])} te entraron {pesos(entradas)} y salieron "
        f"{pesos(salidas)}. "
    )
    if neto >= 0:
        frase += f"Te quedaron {pesos(neto)}."
    else:
        frase += f"Gastaste {pesos(-neto)} más de lo que te entró."
    if promedio is not None:
        if promedio >= 0:
            frase += f" En un mes normal te quedan {pesos(promedio)}."
        else:
            frase += (
                f" En un mes normal gastas {pesos(-promedio)} más de lo que te entra."
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


TONO_BANDA = {
    "bajo": "Vas bien: tu riesgo de atrasarte en tus pagos es bajo.",
    "medio": "Ojo: tu riesgo de atrasarte en tus pagos es medio.",
    "alto": "Cuidado: tu riesgo de atrasarte en tus pagos es alto.",
}


def resumen_riesgo(banda: str, explicacion: Explicacion | None, n: int) -> dict:
    """La banda en palabras y lo que mas mueve el score del cliente, con sus
    propios valores SHAP: lo que lo sube y lo que lo baja."""
    frase = TONO_BANDA.get(banda, "Todavía no tenemos tu nivel de riesgo.")
    if explicacion is None:
        return {"frase": frase, "suben": [], "bajan": []}
    principales = explicacion.principales(n)
    return {
        "frase": frase,
        "suben": [f.frase for f in principales if f.sube],
        "bajan": [f.frase for f in principales if not f.sube],
    }


def resumen_compra(resultado: dict) -> str:
    """La respuesta de "¿me alcanza?" a partir de evaluar_compra."""
    if "error" in resultado:
        return resultado["error"]
    meses_sim = resultado["meses_simulados"]
    sin = resultado["probabilidad_deficit_sin_compra"] * 100
    con = resultado["probabilidad_deficit_con_compra"] * 100
    riesgo = (
        f"Con la compra, en {con:.0f} de cada 100 escenarios simulados te quedarías "
        f"sin dinero algún mes de los próximos {meses_sim}; sin ella, en {sin:.0f}."
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

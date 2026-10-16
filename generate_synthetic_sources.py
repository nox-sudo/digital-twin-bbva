"""
generate_synthetic_sources.py

Genera datos sintéticos que simulan "fuentes externas heterogéneas"
para el proyecto Gemelo Digital Financiero (BBVA - Tecmilenio).

Filosofía: estos datos representan lo que en un banco real vendría
de un core bancario, un procesador de pagos, o APIs de terceros.
Por eso se guardan en formatos mixtos (CSV, JSON) en /data/raw_sources/
y NO dentro de tu estructura bronze/silver/gold — esa carpeta es
exclusiva del pipeline de ingesta.

Sesiones reproducibles: los parametros (semilla, fecha de referencia,
clientes, meses) salen de config/sesion.yaml, y cada flag los
sobreescribe. Todas las fechas se calculan contra la fecha de
referencia, nunca contra el reloj del sistema, asi que los mismos
parametros producen exactamente los mismos archivos cualquier dia y en
cualquier maquina. Si la sesion ya existe en data/sesiones/ y esta
integra, se reutiliza sin regenerar. Detalle en src/common/sesiones.py.

Uso:
    python generate_synthetic_sources.py                 # parametros de config/sesion.yaml
    python generate_synthetic_sources.py --clientes 5000 # sobreescribe uno
    python generate_synthetic_sources.py --forzar        # regenera aunque exista

Salida:
    data/sesiones/<sesion_id>/                     la sesion, con manifest.json
    data/raw_sources/                              la sesion activa (hard links)
        clientes.csv, catalogo_productos.json, cuentas.json,
        cetes_inversiones.csv,
        transacciones/transacciones_YYYY_MM.csv    (una por mes, carga incremental)
"""

import argparse
import calendar
import hashlib
import json
import random
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from dateutil.relativedelta import relativedelta
from faker import Faker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.identificadores import (  # noqa: E402
    ESTADOS_CURP,
    construir_curp,
    construir_rfc,
)
from src.common.sesiones import (  # noqa: E402
    activar_sesion,
    escribir_manifest,
    id_sesion,
    leer_manifest,
    publicar_sesion,
    verificar_sesion,
)

fake = Faker("es_MX")

# Generadores aleatorios exclusivos para la identidad del cliente (PII).
# Separados de los de arriba a proposito: agregar o cambiar columnas de
# PII no altera la secuencia aleatoria del resto del dataset, asi que
# cuentas, saldos y transacciones siguen saliendo iguales.
fake_pii = Faker("es_MX")
rng_pii = random.Random()


def sembrar(semilla: int) -> None:
    """Fija la semilla de todos los generadores aleatorios que usa este
    script. Se llama al inicio de cada generacion, no al importar el
    modulo, para que dos generaciones en el mismo proceso (como en los
    tests) partan del mismo estado."""
    Faker.seed(semilla)
    random.seed(semilla)
    np.random.seed(semilla)
    fake_pii.seed_instance(semilla)
    rng_pii.seed(semilla)


def fecha_entre(fecha_ref: date, desde: relativedelta, hasta: relativedelta) -> date:
    """Fecha aleatoria entre (fecha_ref - desde) y (fecha_ref - hasta).

    Reemplaza a fake.date_between(start_date="-3y", ...) y a
    fake.date_of_birth(), que calculan contra la fecha del sistema: con
    ellos, la misma semilla producia datos distintos cada dia.
    """
    return fake.date_between_dates(
        date_start=fecha_ref - desde, date_end=fecha_ref - hasta
    )


def semilla_mes(semilla: int, anio: int, mes: int) -> int:
    """Semilla propia de cada mes de transacciones, derivada de la semilla
    de la sesion. Asi el contenido de un mes no depende de cuantos meses
    se generan ni en que orden: agregar un mes nuevo (una nueva entrega)
    deja identicos todos los anteriores."""
    digest = hashlib.sha256(f"{semilla}:{anio}:{mes}".encode()).hexdigest()
    return int(digest[:8], 16)


def fecha_de_corte(fecha_base: date, meses_adicionales: int) -> date:
    """Ultimo dia cubierto por la sesion: fin del mes completo mas reciente
    de la fecha base, recorrido meses_adicionales hacia adelante."""
    ultimo_mes = meses_a_generar(fecha_base, 1)[0]
    inicio = date(*ultimo_mes, 1) + relativedelta(months=meses_adicionales)
    return inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1])


def meses_a_generar(fecha_ref: date, n_meses: int) -> list[tuple[int, int]]:
    """Los n_meses completos que terminan en fecha_ref, del mas reciente
    al mas antiguo. Si fecha_ref no es fin de mes, su mes se excluye:
    las transacciones se generan en dias 1-28 de cada mes, y un mes en
    curso produciria transacciones con fecha posterior a fecha_ref."""
    ultimo_dia = calendar.monthrange(fecha_ref.year, fecha_ref.month)[1]
    mes_mas_reciente = fecha_ref.replace(day=1)
    if fecha_ref.day < ultimo_dia:
        mes_mas_reciente -= relativedelta(months=1)
    meses = []
    for i in range(n_meses):
        fecha_mes = mes_mas_reciente - relativedelta(months=i)
        meses.append((fecha_mes.year, fecha_mes.month))
    return meses


# ---------------------------------------------------------------------------
# Catálogos de referencia (esto simula "datos maestros" del banco)
# ---------------------------------------------------------------------------

OCUPACIONES = [
    "Empleado",
    "Independiente",
    "Empresario",
    "Estudiante",
    "Jubilado",
    "Profesionista",
    "Comerciante",
]

CIUDADES = [
    "Ciudad de México",
    "Guadalajara",
    "Monterrey",
    "Culiacán",
    "Puebla",
    "Tijuana",
    "Querétaro",
    "Mérida",
    "León",
    "Toluca",
]

TIPOS_CUENTA = ["cuenta_digital", "tarjeta_credito", "prestamo_personal", "cetes"]

CATEGORIAS_GASTO = [
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
]

COMERCIOS_POR_CATEGORIA = {
    "comida": ["Uber Eats", "Rappi", "Walmart", "Soriana", "Oxxo", "Restaurante Local"],
    "transporte": ["Uber", "Didi", "Gasolinera Pemex", "Metro CDMX"],
    "entretenimiento": ["Netflix", "Spotify", "Cinepolis", "Steam"],
    "servicios": ["CFE", "Telmex", "Izzi", "Agua y Drenaje"],
    "salud": ["Farmacia Guadalajara", "Farmacia del Ahorro", "Consultorio Médico"],
    "educacion": ["Colegiatura", "Platzi", "Udemy"],
    "hogar": ["Home Depot", "Liverpool", "IKEA"],
    "ropa": ["Zara", "Shein", "Nike"],
    "suscripciones": ["Amazon Prime", "Disney+", "HBO Max", "Gimnasio"],
    "otros": ["Transferencia Varios", "Cargo Bancario"],
}

TIPOS_TRANSACCION = [
    "compra",
    "p2p_enviado",
    "p2p_recibido",
    "pago_recurrente",
    "nomina",
    "deposito",
    "retiro",
]

# Perfil financiero de cada cliente: rasgos que se sortean UNA vez y se
# mantienen todo el periodo (ver generar_perfiles). Sin ellos, cada mes
# era un sorteo independiente: el ingreso no tenia relacion con el
# ingreso declarado, la nomina desaparecia meses al azar y la categoria
# de gasto dominante cambiaba de un mes a otro.
PROPORCION_FORMALES = 0.75
TASA_PRESTAMO_PERSONAL = 0.28  # anual, la del catalogo de productos
# Ocupaciones que se generan siempre como ingreso informal (depositos
# irregulares) u siempre como formal (nomina o pension fija).
OCUPACIONES_INFORMALES = {"Independiente", "Comerciante", "Estudiante"}
OCUPACIONES_FORMALES = {"Empleado", "Jubilado"}


# Datos por ciudad para domicilio y telefono: estado, clave de estado
# en la CURP, lada y rango de codigos postales reales de la ciudad.
DATOS_CIUDAD = {
    "Ciudad de México": ("Ciudad de México", "DF", "55", 1000, 16999),
    "Guadalajara": ("Jalisco", "JC", "33", 44100, 44990),
    "Monterrey": ("Nuevo León", "NL", "81", 64000, 64999),
    "Culiacán": ("Sinaloa", "SL", "667", 80000, 80299),
    "Puebla": ("Puebla", "PL", "222", 72000, 72599),
    "Tijuana": ("Baja California", "BC", "664", 22000, 22699),
    "Querétaro": ("Querétaro", "QT", "442", 76000, 76249),
    "Mérida": ("Yucatán", "YN", "999", 97000, 97399),
    "León": ("Guanajuato", "GT", "477", 37000, 37699),
    "Toluca": ("Estado de México", "MC", "722", 50000, 50299),
}

# Ocupaciones sin RFC: no perciben ingresos propios ante el SAT.
OCUPACIONES_SIN_RFC = {"Estudiante"}


def _ascii(texto: str) -> str:
    """Minusculas sin acentos ni espacios, para armar correos."""
    import unicodedata

    plano = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in plano if c.isalnum() and c.isascii())


def generar_identidad(fecha_nacimiento: date, ciudad: str, ocupacion: str) -> dict:
    """PII sintetica y coherente de un cliente: los identificadores se
    calculan a partir de sus propios datos (como en la realidad), asi que
    Silver puede validar que la CURP y el RFC le correspondan.

    Medidas para que ningun dato coincida con el de una persona real:
    - Correo en example.com/.org/.net, dominios reservados que no
      pertenecen a nadie (RFC 2606).
    - Telefono con lada real, pero numero local que empieza en 0: en
      Mexico ningun numero asignado empieza asi.
    """
    sexo = rng_pii.choice(["H", "M"])
    nombre = fake_pii.first_name_male() if sexo == "H" else fake_pii.first_name_female()
    apellido_paterno = fake_pii.last_name()
    apellido_materno = fake_pii.last_name()

    estado, clave_estado, lada, cp_min, cp_max = DATOS_CIUDAD[ciudad]
    # La mayoria nacio en el estado donde vive; una parte en otro estado
    # o en el extranjero (NE).
    sorteo = rng_pii.random()
    if sorteo < 0.70:
        estado_nacimiento = clave_estado
    elif sorteo < 0.98:
        estado_nacimiento = rng_pii.choice([e for e in ESTADOS_CURP if e != "NE"])
    else:
        estado_nacimiento = "NE"

    # Caracter 17 de la CURP: digito si nacio antes de 2000, letra despues.
    if fecha_nacimiento.year < 2000:
        homoclave = rng_pii.choice("0123456789")
    else:
        homoclave = rng_pii.choice("ABCDEFGHIJKLMNPQRSTUVWXYZ")

    numero_local_digitos = 10 - len(lada)
    numero_local = "0" + "".join(
        rng_pii.choice("0123456789") for _ in range(numero_local_digitos - 1)
    )

    usuario = f"{_ascii(nombre.split()[0])}.{_ascii(apellido_paterno)}{rng_pii.randint(1, 99)}"
    dominio = rng_pii.choice(["example.com", "example.org", "example.net"])

    return {
        "nombre": nombre,
        "apellido_paterno": apellido_paterno,
        "apellido_materno": apellido_materno,
        "sexo": sexo,
        "estado_nacimiento": estado_nacimiento,
        "curp": construir_curp(
            nombre,
            apellido_paterno,
            apellido_materno,
            fecha_nacimiento,
            sexo,
            estado_nacimiento,
            homoclave,
        ),
        "rfc": (
            None
            if ocupacion in OCUPACIONES_SIN_RFC
            else construir_rfc(
                nombre, apellido_paterno, apellido_materno, fecha_nacimiento
            )
        ),
        "telefono": lada + numero_local,
        "email": f"{usuario}@{dominio}",
        "calle": fake_pii.street_name(),
        "numero_exterior": fake_pii.building_number(),
        "colonia": f"Colonia {fake_pii.last_name()}",
        "codigo_postal": f"{rng_pii.randint(cp_min, cp_max):05d}",
        "municipio": ciudad,
        "estado": estado,
    }


def generar_clientes(n_clientes: int, fecha_ref: date) -> pd.DataFrame:
    """Genera el perfil demográfico base de los clientes, con su PII."""
    registros = []
    for i in range(n_clientes):
        fecha_nacimiento = fecha_entre(
            fecha_ref, relativedelta(years=70), relativedelta(years=18)
        )
        ingreso_base = np.random.lognormal(
            mean=9.8, sigma=0.5
        )  # distribución realista de ingresos
        ocupacion = random.choice(OCUPACIONES)
        ciudad = random.choice(CIUDADES)
        registros.append(
            {
                "cliente_id": f"CLI-{i+1:06d}",
                "fecha_nacimiento": fecha_nacimiento.isoformat(),
                "ocupacion": ocupacion,
                "ingreso_mensual_declarado": round(float(ingreso_base), 2),
                "ciudad": ciudad,
                "fecha_alta": fecha_entre(
                    fecha_ref, relativedelta(years=3), relativedelta(months=1)
                ).isoformat(),
                **generar_identidad(fecha_nacimiento, ciudad, ocupacion),
            }
        )
    return pd.DataFrame(registros)


def generar_catalogo_productos() -> dict:
    """Catálogo de productos financieros con sus términos (datos maestros, JSON)."""
    return {
        "productos": [
            {
                "producto_id": "cuenta_digital",
                "nombre": "Cuenta Digital",
                "tasa_interes_anual": 0.02,
                "requiere_ingreso_minimo": False,
            },
            {
                "producto_id": "tarjeta_credito",
                "nombre": "Tarjeta de Crédito Clásica",
                "tasa_interes_anual": 0.45,
                "limite_credito_min": 5000,
                "limite_credito_max": 150000,
                "requiere_ingreso_minimo": True,
            },
            {
                "producto_id": "prestamo_personal",
                "nombre": "Préstamo Personal",
                "tasa_interes_anual": 0.28,
                "plazos_meses": [12, 24, 36, 48],
                "requiere_ingreso_minimo": True,
            },
            {
                "producto_id": "cetes",
                "nombre": "CETES / Inversión a Plazo",
                "tasa_interes_anual": 0.105,
                "plazos_dias": [28, 91, 182, 364],
                "requiere_ingreso_minimo": False,
            },
        ]
    }


def generar_cuentas(clientes_df: pd.DataFrame, fecha_ref: date) -> list[dict]:
    """
    Genera cuentas por cliente. No todos tienen todos los productos:
    esto simula un portafolio realista (casi todos tienen cuenta digital,
    menos tienen tarjeta o préstamo, pocos tienen CETES).
    """
    cuentas = []
    contador = 1
    for _, cliente in clientes_df.iterrows():
        # Saldos, limites y prestamos escalan con el ingreso declarado, como
        # los otorgaria un banco: sin esto, un cliente de 8,000 al mes podia
        # tener una linea de 120,000 y cuotas mayores que su ingreso.
        ingreso = float(cliente["ingreso_mensual_declarado"])
        # Toda persona tiene cuenta digital
        cuentas.append(
            {
                "cuenta_id": f"CTA-{contador:06d}",
                "cliente_id": cliente["cliente_id"],
                "tipo_cuenta": TIPOS_CUENTA[0],
                "fecha_apertura": cliente["fecha_alta"],
                "saldo_actual": round(float(ingreso * np.random.uniform(0.2, 2.0)), 2),
                "moneda": "MXN",
                "estatus": "activa",
            }
        )
        contador += 1

        # 55% tiene tarjeta de crédito
        if random.random() < 0.55:
            # Linea de 1 a 3 veces el ingreso mensual, minimo 5,000.
            limite = max(
                5000.0, round(float(ingreso * np.random.uniform(1.0, 3.0)), -2)
            )
            cuentas.append(
                {
                    "cuenta_id": f"CTA-{contador:06d}",
                    "cliente_id": cliente["cliente_id"],
                    "tipo_cuenta": TIPOS_CUENTA[1],
                    "fecha_apertura": fecha_entre(
                        fecha_ref, relativedelta(years=2), relativedelta(months=1)
                    ).isoformat(),
                    "saldo_actual": round(float(np.random.uniform(0, limite * 0.6)), 2),
                    "limite_credito": limite,
                    "moneda": "MXN",
                    "estatus": "activa",
                }
            )
            contador += 1

        # 30% tiene préstamo personal
        if random.random() < 0.30:
            # Monto tal que la cuota quede entre 10 % y 30 % del ingreso (el
            # tope que usaria un analista de credito), para el plazo elegido.
            plazo = random.choice([12, 24, 36, 48])
            cuota_objetivo = ingreso * np.random.uniform(0.10, 0.30)
            tasa_mensual = TASA_PRESTAMO_PERSONAL / 12
            monto = cuota_objetivo * (1 - (1 + tasa_mensual) ** -plazo) / tasa_mensual
            monto = max(10000.0, round(float(monto), -2))
            cuentas.append(
                {
                    "cuenta_id": f"CTA-{contador:06d}",
                    "cliente_id": cliente["cliente_id"],
                    "tipo_cuenta": TIPOS_CUENTA[2],
                    "fecha_apertura": fecha_entre(
                        fecha_ref, relativedelta(years=2), relativedelta(months=1)
                    ).isoformat(),
                    "saldo_actual": round(
                        float(monto * np.random.uniform(0.3, 1.0)), 2
                    ),
                    "monto_original": monto,
                    "plazo_meses": plazo,
                    "moneda": "MXN",
                    "estatus": "activa",
                }
            )
            contador += 1

        # 15% tiene CETES / inversión
        if random.random() < 0.15:
            cuentas.append(
                {
                    "cuenta_id": f"CTA-{contador:06d}",
                    "cliente_id": cliente["cliente_id"],
                    "tipo_cuenta": TIPOS_CUENTA[3],
                    "fecha_apertura": fecha_entre(
                        fecha_ref, relativedelta(years=1), relativedelta(months=1)
                    ).isoformat(),
                    "saldo_actual": round(float(np.random.uniform(2000, 80000)), 2),
                    "moneda": "MXN",
                    "estatus": "activa",
                }
            )
            contador += 1

    return cuentas


def generar_cetes_inversiones(cuentas: list[dict]) -> pd.DataFrame:
    """Detalle específico de las cuentas tipo CETES."""
    registros = []
    plazos = [28, 91, 182, 364]
    for cuenta in cuentas:
        if cuenta["tipo_cuenta"] == "cetes":
            plazo = random.choice(plazos)
            fecha_inicio = datetime.fromisoformat(cuenta["fecha_apertura"])
            registros.append(
                {
                    "cuenta_id": cuenta["cuenta_id"],
                    "monto_invertido": cuenta["saldo_actual"],
                    "plazo_dias": plazo,
                    "tasa_interes_anual": 0.105,
                    "fecha_inicio": fecha_inicio.date().isoformat(),
                    "fecha_vencimiento": (fecha_inicio + timedelta(days=plazo))
                    .date()
                    .isoformat(),
                }
            )
    return pd.DataFrame(registros)


def _cuota_prestamo(monto: float, plazo_meses: int) -> float:
    """Pago mensual fijo de un prestamo (amortizacion francesa)."""
    tasa_mensual = TASA_PRESTAMO_PERSONAL / 12
    return monto * tasa_mensual / (1 - (1 + tasa_mensual) ** -plazo_meses)


def generar_perfiles(
    clientes_df: pd.DataFrame, cuentas: list[dict], semilla: int
) -> dict[str, dict]:
    """Rasgos financieros estables de cada cliente.

    Usa un generador propio, derivado de la semilla de la sesion, en vez del
    estado global de random/NumPy: asi agregar los perfiles no cambia ni un
    valor de clientes, cuentas o CETES, y el perfil de un cliente no depende
    del mes que se este generando.

    Rasgos:
      formal          nomina quincenal fija (True) o depositos irregulares
      ingreso_base    ingreso mensual esperado, anclado al declarado
      volatilidad     dispersion mes a mes del ingreso (0 si es formal)
      tasa_gasto      fraccion del ingreso que se va en compras
      pesos_categoria preferencia estable por categoria de gasto
      puntualidad     probabilidad de pagar cada deuda en un mes dado
      cuotas          pago mensual fijo por cuenta de deuda
    """
    rng = np.random.default_rng(int(semilla_mes(semilla, 0, 0)))
    deudas_por_cliente: dict[str, list[dict]] = {}
    for c in cuentas:
        if c["tipo_cuenta"] in ("tarjeta_credito", "prestamo_personal"):
            deudas_por_cliente.setdefault(c["cliente_id"], []).append(c)

    perfiles = {}
    for _, cliente in clientes_df.iterrows():
        declarado = float(cliente["ingreso_mensual_declarado"])
        if cliente["ocupacion"] in OCUPACIONES_FORMALES:
            formal = True
        elif cliente["ocupacion"] in OCUPACIONES_INFORMALES:
            formal = False
        else:
            formal = bool(rng.random() < PROPORCION_FORMALES)

        # La gente tiende a declarar algo de mas: lo observado queda entre
        # 80 % y 105 % de lo declarado, un poco menos para informales.
        ingreso_base = declarado * (
            rng.uniform(0.85, 1.05) if formal else rng.uniform(0.75, 1.0)
        )
        # Un 15 % de los clientes gasta de forma cronica mas de lo que gana:
        # es el segmento que el riesgo deberia poder distinguir.
        if rng.random() < 0.15:
            tasa_gasto = rng.uniform(0.95, 1.20)
        else:
            tasa_gasto = rng.uniform(0.45, 0.85)
        # Puntualidad: casi todos pagan casi siempre; un 12 % es irregular.
        if rng.random() < 0.12:
            puntualidad = rng.uniform(0.50, 0.80)
        else:
            puntualidad = rng.uniform(0.95, 1.0)

        cuotas = {}
        for deuda in deudas_por_cliente.get(cliente["cliente_id"], []):
            if deuda["tipo_cuenta"] == "prestamo_personal":
                cuota = _cuota_prestamo(deuda["monto_original"], deuda["plazo_meses"])
            else:  # tarjeta: paga una fraccion estable de su saldo
                cuota = max(300.0, deuda["saldo_actual"] * rng.uniform(0.04, 0.12))
            cuotas[deuda["cuenta_id"]] = round(float(cuota), 2)

        perfiles[cliente["cliente_id"]] = {
            "formal": formal,
            "ingreso_base": ingreso_base,
            "volatilidad": 0.0 if formal else float(rng.uniform(0.20, 0.45)),
            "tasa_gasto": float(tasa_gasto),
            # Dirichlet con alfa < 1: cada cliente concentra su gasto en
            # pocas categorias, y esas categorias se mantienen.
            "pesos_categoria": rng.dirichlet(np.full(len(CATEGORIAS_GASTO), 0.7)),
            "puntualidad": float(puntualidad),
            "cuotas": cuotas,
        }
    return perfiles


def _montos_que_suman(total: float, n: int) -> np.ndarray:
    """Reparte un total en n montos positivos y desiguales."""
    proporciones = np.random.dirichlet(np.ones(n))
    return np.maximum(np.round(total * proporciones, 2), 20.0)


def generar_transacciones_mes(
    cuentas: list[dict], anio: int, mes: int, perfiles: dict[str, dict]
) -> pd.DataFrame:
    """
    Genera transacciones de un mes específico para todas las cuentas activas.
    Simula: ingreso (nómina quincenal o depósitos irregulares), gasto por
    categoría, P2P, pagos de deuda y retiros. Los montos salen del perfil
    estable de cada cliente (generar_perfiles) más el ruido propio del mes.
    """
    registros = []
    tx_id = 1
    cuentas_digitales = [c for c in cuentas if c["tipo_cuenta"] == "cuenta_digital"]

    deudas_por_cliente: dict[str, list[dict]] = {}
    for c in cuentas:
        if c["tipo_cuenta"] in ("tarjeta_credito", "prestamo_personal"):
            deudas_por_cliente.setdefault(c["cliente_id"], []).append(c)

    for cuenta in cuentas_digitales:
        cliente_id = cuenta["cliente_id"]
        cuenta_id = cuenta["cuenta_id"]

        perfil = perfiles[cliente_id]
        ultimo_dia = calendar.monthrange(anio, mes)[1]

        def agregar(fecha_mov, tipo, categoria, monto, contraparte, **extra):
            nonlocal tx_id
            registros.append(
                {
                    "transaccion_id": f"TX-{anio}{mes:02d}-{tx_id:07d}",
                    "cuenta_id": cuenta_id,
                    "cliente_id": cliente_id,
                    "fecha": fecha_mov.isoformat(),
                    "tipo_transaccion": tipo,
                    "categoria": categoria,
                    "monto": monto,
                    "contraparte": contraparte,
                    **extra,
                }
            )
            tx_id += 1

        # Ingreso. Formal: misma nomina las dos quincenas, todos los meses.
        # Informal: el ingreso del mes varia alrededor del base (lognormal con
        # media 1) y llega en 2 a 6 depositos; algun mes puede no llegar nada.
        if perfil["formal"]:
            quincena = round(perfil["ingreso_base"] / 2, 2)
            for dia_pago in [15, ultimo_dia]:
                agregar(
                    date(anio, mes, dia_pago),
                    "nomina",
                    "ingreso",
                    quincena,
                    "Depósito de Nómina",
                )
        elif random.random() > 0.06:
            sigma = perfil["volatilidad"]
            ingreso_mes = perfil["ingreso_base"] * np.random.lognormal(
                -(sigma**2) / 2, sigma
            )
            n_depositos = random.randint(2, 6)
            for monto in _montos_que_suman(ingreso_mes, n_depositos):
                agregar(
                    date(anio, mes, random.randint(1, ultimo_dia)),
                    "deposito",
                    "ingreso",
                    float(monto),
                    random.choice(["Depósito en efectivo", "Transferencia de cliente"]),
                )

        # Gasto: el total del mes es una fraccion estable del ingreso base,
        # con +-15 % de ruido y un repunte en diciembre; las categorias salen
        # de las preferencias del cliente.
        estacional = 1.15 if mes == 12 else 1.0
        gasto_mes = (
            perfil["ingreso_base"]
            * perfil["tasa_gasto"]
            * estacional
            * np.random.uniform(0.85, 1.15)
        )
        n_transacciones = random.randint(8, 25)
        categorias = np.random.choice(
            CATEGORIAS_GASTO, size=n_transacciones, p=perfil["pesos_categoria"]
        )
        for categoria, monto in zip(
            categorias, _montos_que_suman(gasto_mes, n_transacciones)
        ):
            agregar(
                date(anio, mes, random.randint(1, 28)),
                "compra",
                str(categoria),
                -float(monto),  # negativo = salida de dinero
                random.choice(COMERCIOS_POR_CATEGORIA[categoria]),
            )

        # P2P ocasional (tipo SPEI entre personas)
        if random.random() < 0.4:
            monto_p2p = round(float(np.random.uniform(100, 2000)), 2)
            enviado = random.random() < 0.5
            agregar(
                date(anio, mes, random.randint(1, 28)),
                "p2p_enviado" if enviado else "p2p_recibido",
                "transferencia_personal",
                -monto_p2p if enviado else monto_p2p,
                # contraparte describe el movimiento (nunca una persona);
                # el nombre de la otra persona va aparte porque es PII
                # de alguien que ni siquiera es cliente.
                "Transferencia SPEI",
                contraparte_persona=fake.name(),
            )

        # Pago de deudas: la cuota es fija por cuenta (perfil); que se pague
        # o no en un mes depende de la puntualidad del cliente. Un cliente
        # impuntual deja huella en los datos, no solo en la etiqueta.
        for deuda in deudas_por_cliente.get(cliente_id, []):
            if random.random() < perfil["puntualidad"]:
                agregar(
                    date(anio, mes, random.choice([5, 10, 20])),
                    "pago_recurrente",
                    "pago_deuda",
                    -perfil["cuotas"][deuda["cuenta_id"]],
                    (
                        "Pago Tarjeta de Crédito"
                        if deuda["tipo_cuenta"] == "tarjeta_credito"
                        else "Pago Préstamo Personal"
                    ),
                )

        # Retiro de efectivo ocasional, proporcional al ingreso
        if random.random() < 0.2:
            monto_retiro = perfil["ingreso_base"] * np.random.uniform(0.02, 0.10)
            agregar(
                date(anio, mes, random.randint(1, 28)),
                "retiro",
                "efectivo",
                -round(float(max(200.0, monto_retiro)), 2),
                "Retiro Cajero",
            )

    return pd.DataFrame(registros)


def version_generador() -> str:
    """Huella del codigo de este script. Se guarda en el manifest: si el
    generador cambia, una sesion vieja se sigue reutilizando tal cual
    (son sus datos), pero queda registrado que se genero con otra version."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


def resolver_parametros(args) -> dict:
    """Parametros de la sesion: config/sesion.yaml, sobreescrito por los
    flags que se hayan pasado."""
    config = {}
    if args.config and Path(args.config).exists():
        with open(args.config, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    def valor(flag, clave, default):
        return flag if flag is not None else config.get(clave, default)

    return {
        "semilla": int(valor(args.semilla, "semilla", 42)),
        "fecha_referencia": str(
            valor(args.fecha_referencia, "fecha_referencia", date.today().isoformat())
        ),
        "clientes": int(valor(args.clientes, "clientes", 500)),
        "meses": int(valor(args.meses, "meses", 12)),
        "meses_adicionales": int(valor(args.meses_adicionales, "meses_adicionales", 0)),
    }


def generar_fuentes(out_dir: Path, parametros: dict) -> dict:
    """Genera todas las fuentes de una sesion en out_dir. Devuelve los
    conteos por entidad para el manifest."""
    sembrar(parametros["semilla"])
    # Clientes y cuentas se anclan a la fecha base, no a la de corte: una
    # entrega nueva trae los mismos clientes con un mes mas de movimientos,
    # no una poblacion distinta.
    fecha_ref = date.fromisoformat(parametros["fecha_referencia"])
    corte = fecha_de_corte(fecha_ref, parametros["meses_adicionales"])
    total_meses = parametros["meses"] + parametros["meses_adicionales"]
    (out_dir / "transacciones").mkdir(parents=True, exist_ok=True)

    print(f"Generando {parametros['clientes']} clientes...")
    clientes_df = generar_clientes(parametros["clientes"], fecha_ref)
    clientes_df.to_csv(out_dir / "clientes.csv", index=False)

    print("Generando catálogo de productos...")
    catalogo = generar_catalogo_productos()
    with open(out_dir / "catalogo_productos.json", "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=2)

    print("Generando cuentas...")
    cuentas = generar_cuentas(clientes_df, fecha_ref)
    with open(out_dir / "cuentas.json", "w", encoding="utf-8") as f:
        json.dump(cuentas, f, ensure_ascii=False, indent=2)

    print("Generando detalle de CETES...")
    cetes_df = generar_cetes_inversiones(cuentas)
    cetes_df.to_csv(out_dir / "cetes_inversiones.csv", index=False)

    perfiles = generar_perfiles(clientes_df, cuentas, parametros["semilla"])

    print(f"Generando transacciones para {total_meses} meses (corte {corte})...")
    total_tx = 0
    for anio, mes in meses_a_generar(corte, total_meses):
        semilla = semilla_mes(parametros["semilla"], anio, mes)
        random.seed(semilla)
        np.random.seed(semilla)
        Faker.seed(semilla)  # contraparte_persona (P2P) usa Faker
        tx_df = generar_transacciones_mes(cuentas, anio, mes, perfiles)
        archivo = out_dir / "transacciones" / f"transacciones_{anio}_{mes:02d}.csv"
        tx_df.to_csv(archivo, index=False)
        total_tx += len(tx_df)
        print(f"  -> {archivo.name} ({len(tx_df)} filas)")

    return {
        "clientes": len(clientes_df),
        "cuentas": len(cuentas),
        "cetes_inversiones": len(cetes_df),
        "transacciones": total_tx,
    }


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Generador de datos sintéticos - Gemelo Digital Financiero"
    )
    parser.add_argument("--config", default="config/sesion.yaml")
    parser.add_argument("--clientes", type=int, help="Número de clientes a generar")
    parser.add_argument("--meses", type=int, help="Meses de historial transaccional")
    parser.add_argument("--semilla", type=int)
    parser.add_argument("--fecha-referencia", help="YYYY-MM-DD; 'hoy' de los datos")
    parser.add_argument(
        "--meses-adicionales",
        type=int,
        help="Entregas mensuales posteriores a la fecha de referencia (ver demo.sh nueva-entrega)",
    )
    parser.add_argument(
        "--out",
        default="data/raw_sources",
        help="Carpeta de la sesion activa (la que lee Bronze)",
    )
    parser.add_argument("--sesiones-dir", default="data/sesiones")
    parser.add_argument(
        "--forzar", action="store_true", help="Regenera aunque la sesion ya exista"
    )
    args = parser.parse_args(argv)

    parametros = resolver_parametros(args)
    sesion_id = id_sesion(
        parametros["semilla"],
        date.fromisoformat(parametros["fecha_referencia"]),
        parametros["clientes"],
        parametros["meses"],
        parametros["meses_adicionales"],
    )
    sesiones_dir = Path(args.sesiones_dir)
    carpeta_sesion = sesiones_dir / sesion_id
    print(f"Sesion: {sesion_id}")

    problemas = verificar_sesion(carpeta_sesion) if carpeta_sesion.exists() else None
    if problemas == [] and not args.forzar:
        manifest = leer_manifest(carpeta_sesion)
        print(
            f"Sesion existente e integra (generada {manifest['generada_en']}): se reutiliza."
        )
        if manifest["version_generador"] != version_generador():
            print(
                "  Aviso: se genero con otra version del generador. Se reutiliza tal "
                "cual; usa --forzar para regenerarla con la version actual."
            )
    else:
        if problemas:
            print("La sesion existe pero no esta integra, se regenera:")
            for problema in problemas:
                print(f"  - {problema}")
        sesiones_dir.mkdir(parents=True, exist_ok=True)
        carpeta_temporal = Path(
            tempfile.mkdtemp(prefix=f".{sesion_id}.", dir=sesiones_dir)
        )
        conteos = generar_fuentes(carpeta_temporal, parametros)
        escribir_manifest(
            carpeta_temporal, sesion_id, parametros, conteos, version_generador()
        )
        publicar_sesion(carpeta_temporal, carpeta_sesion)
        manifest = leer_manifest(carpeta_sesion)

    activar_sesion(carpeta_sesion, Path(args.out))

    conteos = manifest["conteos"]
    print("\n=== Resumen ===")
    print(f"Sesion activa: {sesion_id}")
    print(f"Clientes: {conteos['clientes']}")
    print(f"Cuentas: {conteos['cuentas']}")
    print(f"Transacciones totales: {conteos['transacciones']}")
    print(f"Archivos en: {carpeta_sesion.resolve()}")
    print(f"Activa en: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()

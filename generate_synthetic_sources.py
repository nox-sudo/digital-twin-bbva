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
    "retiro",
]


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
        # Toda persona tiene cuenta digital
        cuentas.append(
            {
                "cuenta_id": f"CTA-{contador:06d}",
                "cliente_id": cliente["cliente_id"],
                "tipo_cuenta": TIPOS_CUENTA[0],
                "fecha_apertura": cliente["fecha_alta"],
                "saldo_actual": round(float(np.random.uniform(500, 50000)), 2),
                "moneda": "MXN",
                "estatus": "activa",
            }
        )
        contador += 1

        # 55% tiene tarjeta de crédito
        if random.random() < 0.55:
            limite = round(float(np.random.uniform(5000, 120000)), -2)
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
            monto = round(float(np.random.uniform(10000, 200000)), -2)
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
                    "plazo_meses": random.choice([12, 24, 36, 48]),
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


def generar_transacciones_mes(cuentas: list[dict], anio: int, mes: int) -> pd.DataFrame:
    """
    Genera transacciones de un mes específico para todas las cuentas activas.
    Simula: nómina quincenal, gasto variable por categoría, P2P, pagos recurrentes.
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

        # Nómina quincenal (día 15 y último día del mes) - no todos son empleados formales
        if random.random() < 0.75:
            ultimo_dia = calendar.monthrange(anio, mes)[1]
            # Mismo monto en ambas quincenas del mes: un empleado con
            # salario fijo no cobra un monto distinto cada quincena.
            monto_nomina = round(float(np.random.uniform(4000, 25000)), 2)
            for dia_pago in [15, ultimo_dia]:
                fecha = date(anio, mes, dia_pago)
                registros.append(
                    {
                        "transaccion_id": f"TX-{anio}{mes:02d}-{tx_id:07d}",
                        "cuenta_id": cuenta_id,
                        "cliente_id": cliente_id,
                        "fecha": fecha.isoformat(),
                        "tipo_transaccion": "nomina",
                        "categoria": "ingreso",
                        "monto": monto_nomina,
                        "contraparte": "Depósito de Nómina",
                    }
                )
                tx_id += 1

        # Gasto variable: entre 8 y 25 transacciones al mes por cuenta
        n_transacciones = random.randint(8, 25)
        for _ in range(n_transacciones):
            dia = random.randint(1, 28)
            fecha = date(anio, mes, dia)
            categoria = random.choice(CATEGORIAS_GASTO)
            comercio = random.choice(COMERCIOS_POR_CATEGORIA[categoria])

            # Suscripciones y servicios tienen montos más estables (menos ruido)
            if categoria in ("suscripciones", "servicios"):
                monto = round(float(np.random.uniform(100, 800)), 2)
            else:
                monto = round(float(np.random.uniform(50, 3000)), 2)

            registros.append(
                {
                    "transaccion_id": f"TX-{anio}{mes:02d}-{tx_id:07d}",
                    "cuenta_id": cuenta_id,
                    "cliente_id": cliente_id,
                    "fecha": fecha.isoformat(),
                    "tipo_transaccion": "compra",
                    "categoria": categoria,
                    "monto": -monto,  # negativo = salida de dinero
                    "contraparte": comercio,
                }
            )
            tx_id += 1

        # P2P ocasional (tipo SPEI entre personas)
        if random.random() < 0.4:
            dia = random.randint(1, 28)
            fecha = date(anio, mes, dia)
            monto_p2p = round(float(np.random.uniform(100, 2000)), 2)
            enviado = random.random() < 0.5
            registros.append(
                {
                    "transaccion_id": f"TX-{anio}{mes:02d}-{tx_id:07d}",
                    "cuenta_id": cuenta_id,
                    "cliente_id": cliente_id,
                    "fecha": fecha.isoformat(),
                    "tipo_transaccion": "p2p_enviado" if enviado else "p2p_recibido",
                    "categoria": "transferencia_personal",
                    "monto": -monto_p2p if enviado else monto_p2p,
                    "contraparte": fake.name(),
                }
            )
            tx_id += 1

        # Pago recurrente: pago automático mensual hacia deudas propias del cliente
        for deuda in deudas_por_cliente.get(cliente_id, []):
            if random.random() < 0.85:
                dia = random.choice([5, 10, 20])
                fecha = date(anio, mes, dia)
                if deuda["tipo_cuenta"] == "tarjeta_credito":
                    monto_pago = round(
                        float(np.random.uniform(300, deuda["limite_credito"] * 0.15)), 2
                    )
                    contraparte = "Pago Tarjeta de Crédito"
                else:  # prestamo_personal
                    monto_pago = round(float(np.random.uniform(500, 4000)), 2)
                    contraparte = "Pago Préstamo Personal"
                registros.append(
                    {
                        "transaccion_id": f"TX-{anio}{mes:02d}-{tx_id:07d}",
                        "cuenta_id": cuenta_id,
                        "cliente_id": cliente_id,
                        "fecha": fecha.isoformat(),
                        "tipo_transaccion": "pago_recurrente",
                        "categoria": "pago_deuda",
                        "monto": -monto_pago,
                        "contraparte": contraparte,
                    }
                )
                tx_id += 1

        # Retiro de efectivo ocasional
        if random.random() < 0.2:
            dia = random.randint(1, 28)
            fecha = date(anio, mes, dia)
            monto_retiro = round(float(np.random.uniform(200, 3000)), 2)
            registros.append(
                {
                    "transaccion_id": f"TX-{anio}{mes:02d}-{tx_id:07d}",
                    "cuenta_id": cuenta_id,
                    "cliente_id": cliente_id,
                    "fecha": fecha.isoformat(),
                    "tipo_transaccion": "retiro",
                    "categoria": "efectivo",
                    "monto": -monto_retiro,
                    "contraparte": "Retiro Cajero",
                }
            )
            tx_id += 1

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
    }


def generar_fuentes(out_dir: Path, parametros: dict) -> dict:
    """Genera todas las fuentes de una sesion en out_dir. Devuelve los
    conteos por entidad para el manifest."""
    sembrar(parametros["semilla"])
    fecha_ref = date.fromisoformat(parametros["fecha_referencia"])
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

    print(f"Generando transacciones para {parametros['meses']} meses...")
    total_tx = 0
    for anio, mes in meses_a_generar(fecha_ref, parametros["meses"]):
        tx_df = generar_transacciones_mes(cuentas, anio, mes)
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

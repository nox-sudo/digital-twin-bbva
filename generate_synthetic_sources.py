"""
generate_synthetic_sources.py

Genera datos sintéticos que simulan "fuentes externas heterogéneas"
para el proyecto Gemelo Digital Financiero (BBVA - Tecmilenio).

Filosofía: estos datos representan lo que en un banco real vendría
de un core bancario, un procesador de pagos, o APIs de terceros.
Por eso se guardan en formatos mixtos (CSV, JSON) en /data/raw_sources/
y NO dentro de tu estructura bronze/silver/gold — esa carpeta es
exclusiva de tu pipeline de ingesta.

Uso:
    python generate_synthetic_sources.py --clientes 500 --meses 12

Salida:
    data/raw_sources/clientes.csv
    data/raw_sources/catalogo_productos.json
    data/raw_sources/cuentas.json
    data/raw_sources/cetes_inversiones.csv
    data/raw_sources/transacciones/transacciones_YYYY_MM.csv  (una por mes, simula carga incremental)
"""

import argparse
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

fake = Faker("es_MX")
Faker.seed(42)
random.seed(42)
np.random.seed(42)

# ---------------------------------------------------------------------------
# Catálogos de referencia (esto simula "datos maestros" del banco)
# ---------------------------------------------------------------------------

OCUPACIONES = [
    "Empleado", "Independiente", "Empresario", "Estudiante",
    "Jubilado", "Profesionista", "Comerciante",
]

CIUDADES = [
    "Ciudad de México", "Guadalajara", "Monterrey", "Culiacán",
    "Puebla", "Tijuana", "Querétaro", "Mérida", "León", "Toluca",
]

TIPOS_CUENTA = ["cuenta_digital", "tarjeta_credito", "prestamo_personal", "cetes"]

CATEGORIAS_GASTO = [
    "comida", "transporte", "entretenimiento", "servicios",
    "salud", "educacion", "hogar", "ropa", "suscripciones", "otros",
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

TIPOS_TRANSACCION = ["compra", "p2p_enviado", "p2p_recibido", "pago_recurrente", "nomina", "retiro"]


def generar_clientes(n_clientes: int) -> pd.DataFrame:
    """Genera el perfil demográfico base de los clientes."""
    registros = []
    for i in range(n_clientes):
        fecha_nacimiento = fake.date_of_birth(minimum_age=18, maximum_age=70)
        ingreso_base = np.random.lognormal(mean=9.8, sigma=0.5)  # distribución realista de ingresos
        registros.append({
            "cliente_id": f"CLI-{i+1:06d}",
            "nombre": fake.name(),
            "fecha_nacimiento": fecha_nacimiento.isoformat(),
            "ocupacion": random.choice(OCUPACIONES),
            "ingreso_mensual_declarado": round(float(ingreso_base), 2),
            "ciudad": random.choice(CIUDADES),
            "fecha_alta": fake.date_between(start_date="-3y", end_date="-1M").isoformat(),
            "email": fake.email(),
        })
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


def generar_cuentas(clientes_df: pd.DataFrame) -> list[dict]:
    """
    Genera cuentas por cliente. No todos tienen todos los productos:
    esto simula un portafolio realista (casi todos tienen cuenta digital,
    menos tienen tarjeta o préstamo, pocos tienen CETES).
    """
    cuentas = []
    contador = 1
    for _, cliente in clientes_df.iterrows():
        # Toda persona tiene cuenta digital
        cuentas.append({
            "cuenta_id": f"CTA-{contador:06d}",
            "cliente_id": cliente["cliente_id"],
            "tipo_cuenta": "cuenta_digital",
            "fecha_apertura": cliente["fecha_alta"],
            "saldo_actual": round(float(np.random.uniform(500, 50000)), 2),
            "moneda": "MXN",
            "estatus": "activa",
        })
        contador += 1

        # 55% tiene tarjeta de crédito
        if random.random() < 0.55:
            limite = round(float(np.random.uniform(5000, 120000)), -2)
            cuentas.append({
                "cuenta_id": f"CTA-{contador:06d}",
                "cliente_id": cliente["cliente_id"],
                "tipo_cuenta": "tarjeta_credito",
                "fecha_apertura": fake.date_between(start_date="-2y", end_date="-1M").isoformat(),
                "saldo_actual": round(float(np.random.uniform(0, limite * 0.6)), 2),
                "limite_credito": limite,
                "moneda": "MXN",
                "estatus": "activa",
            })
            contador += 1

        # 30% tiene préstamo personal
        if random.random() < 0.30:
            monto = round(float(np.random.uniform(10000, 200000)), -2)
            cuentas.append({
                "cuenta_id": f"CTA-{contador:06d}",
                "cliente_id": cliente["cliente_id"],
                "tipo_cuenta": "prestamo_personal",
                "fecha_apertura": fake.date_between(start_date="-2y", end_date="-1M").isoformat(),
                "saldo_actual": round(float(monto * np.random.uniform(0.3, 1.0)), 2),
                "monto_original": monto,
                "plazo_meses": random.choice([12, 24, 36, 48]),
                "moneda": "MXN",
                "estatus": "activa",
            })
            contador += 1

        # 15% tiene CETES / inversión
        if random.random() < 0.15:
            cuentas.append({
                "cuenta_id": f"CTA-{contador:06d}",
                "cliente_id": cliente["cliente_id"],
                "tipo_cuenta": "cetes",
                "fecha_apertura": fake.date_between(start_date="-1y", end_date="-1M").isoformat(),
                "saldo_actual": round(float(np.random.uniform(2000, 80000)), 2),
                "moneda": "MXN",
                "estatus": "activa",
            })
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
            registros.append({
                "cuenta_id": cuenta["cuenta_id"],
                "monto_invertido": cuenta["saldo_actual"],
                "plazo_dias": plazo,
                "tasa_interes_anual": 0.105,
                "fecha_inicio": fecha_inicio.date().isoformat(),
                "fecha_vencimiento": (fecha_inicio + timedelta(days=plazo)).date().isoformat(),
            })
    return pd.DataFrame(registros)


def generar_transacciones_mes(cuentas: list[dict], anio: int, mes: int) -> pd.DataFrame:
    """
    Genera transacciones de un mes específico para todas las cuentas activas.
    Simula: nómina quincenal, gasto variable por categoría, P2P, pagos recurrentes.
    """
    registros = []
    tx_id = 1
    cuentas_digitales = [c for c in cuentas if c["tipo_cuenta"] == "cuenta_digital"]

    for cuenta in cuentas_digitales:
        cliente_id = cuenta["cliente_id"]
        cuenta_id = cuenta["cuenta_id"]

        # Nómina quincenal (día 15 y último día del mes) - no todos son empleados formales
        if random.random() < 0.75:
            for dia_pago in [15, 28]:
                try:
                    fecha = date(anio, mes, dia_pago)
                except ValueError:
                    fecha = date(anio, mes, 28)
                monto_nomina = round(float(np.random.uniform(4000, 25000)), 2)
                registros.append({
                    "transaccion_id": f"TX-{anio}{mes:02d}-{tx_id:07d}",
                    "cuenta_id": cuenta_id,
                    "cliente_id": cliente_id,
                    "fecha": fecha.isoformat(),
                    "tipo_transaccion": "nomina",
                    "categoria": "ingreso",
                    "monto": monto_nomina,
                    "contraparte": "Depósito de Nómina",
                })
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

            registros.append({
                "transaccion_id": f"TX-{anio}{mes:02d}-{tx_id:07d}",
                "cuenta_id": cuenta_id,
                "cliente_id": cliente_id,
                "fecha": fecha.isoformat(),
                "tipo_transaccion": "compra",
                "categoria": categoria,
                "monto": -monto,  # negativo = salida de dinero
                "contraparte": comercio,
            })
            tx_id += 1

        # P2P ocasional (tipo SPEI entre personas)
        if random.random() < 0.4:
            dia = random.randint(1, 28)
            fecha = date(anio, mes, dia)
            monto_p2p = round(float(np.random.uniform(100, 2000)), 2)
            enviado = random.random() < 0.5
            registros.append({
                "transaccion_id": f"TX-{anio}{mes:02d}-{tx_id:07d}",
                "cuenta_id": cuenta_id,
                "cliente_id": cliente_id,
                "fecha": fecha.isoformat(),
                "tipo_transaccion": "p2p_enviado" if enviado else "p2p_recibido",
                "categoria": "transferencia_personal",
                "monto": -monto_p2p if enviado else monto_p2p,
                "contraparte": fake.name(),
            })
            tx_id += 1

    return pd.DataFrame(registros)


def main():
    parser = argparse.ArgumentParser(description="Generador de datos sintéticos - Gemelo Digital Financiero")
    parser.add_argument("--clientes", type=int, default=500, help="Número de clientes a generar")
    parser.add_argument("--meses", type=int, default=12, help="Meses de historial transaccional")
    parser.add_argument("--out", type=str, default="data/raw_sources", help="Carpeta de salida")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "transacciones").mkdir(parents=True, exist_ok=True)

    print(f"Generando {args.clientes} clientes...")
    clientes_df = generar_clientes(args.clientes)
    clientes_df.to_csv(out_dir / "clientes.csv", index=False)
    print(f"  -> {out_dir / 'clientes.csv'} ({len(clientes_df)} filas)")

    print("Generando catálogo de productos...")
    catalogo = generar_catalogo_productos()
    with open(out_dir / "catalogo_productos.json", "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=2)
    print(f"  -> {out_dir / 'catalogo_productos.json'}")

    print("Generando cuentas...")
    cuentas = generar_cuentas(clientes_df)
    with open(out_dir / "cuentas.json", "w", encoding="utf-8") as f:
        json.dump(cuentas, f, ensure_ascii=False, indent=2)
    print(f"  -> {out_dir / 'cuentas.json'} ({len(cuentas)} cuentas)")

    print("Generando detalle de CETES...")
    cetes_df = generar_cetes_inversiones(cuentas)
    cetes_df.to_csv(out_dir / "cetes_inversiones.csv", index=False)
    print(f"  -> {out_dir / 'cetes_inversiones.csv'} ({len(cetes_df)} filas)")

    print(f"Generando transacciones para {args.meses} meses...")
    hoy = date.today()
    total_tx = 0
    for i in range(args.meses):
        mes_idx = hoy.month - i - 1
        anio = hoy.year + (mes_idx // 12)
        mes = (mes_idx % 12) + 1
        tx_df = generar_transacciones_mes(cuentas, anio, mes)
        archivo = out_dir / "transacciones" / f"transacciones_{anio}_{mes:02d}.csv"
        tx_df.to_csv(archivo, index=False)
        total_tx += len(tx_df)
        print(f"  -> {archivo} ({len(tx_df)} filas)")

    print("\n=== Resumen ===")
    print(f"Clientes: {len(clientes_df)}")
    print(f"Cuentas: {len(cuentas)}")
    print(f"Transacciones totales: {total_tx}")
    print(f"Archivos generados en: {out_dir.resolve()}")


if __name__ == "__main__":
    main()

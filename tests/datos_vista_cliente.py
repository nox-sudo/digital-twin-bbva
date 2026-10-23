"""
tests/datos_vista_cliente.py

Gold y modelo minimos para la vista "Mi resumen" y la explicacion SHAP
(test_vista_cliente.py, test_explicacion.py). Cuatro clientes:

  - CLI-SM:  un salario minimo de nomina, sin tarjeta ni prestamo.
  - CLI-20K: nomina de $20,000, con tarjeta y prestamo.
  - CLI-VAR: depositos irregulares, con quincenas sin ingreso y un prestamo.
  - CLI-SIN: nomina de $20,000, sin credito y con ahorro.

El modelo se entrena con clientes sinteticos que tienen las mismas columnas
que gold_features_cliente; la probabilidad de Gold es la del modelo, como la
publicaria predict_risk.py.
"""

import datetime as dt
import sys
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd
import yaml
from xgboost import XGBClassifier

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from src.gold.risk_features import preparar_para_modelo  # noqa: E402

with open(RAIZ / "config" / "ingresos.yaml", encoding="utf-8") as f:
    SALARIO_MINIMO = float(yaml.safe_load(f)["salario_minimo_mensual"])

MESES = [f"2026-{m:02d}" for m in range(1, 13)]
CATEGORIAS = ["comida", "transporte", "servicios", "hogar", "ropa", "salud"]
NUMERICAS = [
    "ingreso_mensual_promedio",
    "estabilidad_ingreso",
    "gasto_mensual_total",
    "gasto_promedio_3_meses",
    "capacidad_ahorro",
    "saldo_liquido_disponible",
    "ratio_endeudamiento",
    "uso_linea_credito",
    "frecuencia_transacciones",
    "actividad_p2p",
    "edad_anios",
    "antiguedad_dias",
    "ingreso_declarado",
    "numero_productos",
    "tiene_tarjeta_credito",
    "tiene_prestamo_personal",
    "proporcion_retiros",
]


def features_sinteticas(n: int, semilla: int = 7) -> pd.DataFrame:
    """Clientes sinteticos con las columnas de gold_features_cliente."""
    rng = np.random.default_rng(semilla)
    ingreso = rng.lognormal(np.log(15000), 0.5, n)
    gasto = ingreso * rng.uniform(0.5, 1.2, n)
    tarjeta = rng.integers(0, 2, n)
    prestamo = rng.integers(0, 2, n)
    df = pd.DataFrame(
        {
            "cliente_id": [f"SIN-{i:03d}" for i in range(n)],
            "ingreso_mensual_promedio": ingreso,
            "estabilidad_ingreso": ingreso * rng.uniform(0, 0.6, n),
            "gasto_mensual_total": gasto * 0.7,
            "gasto_promedio_3_meses": gasto * rng.uniform(0.8, 1.2, n),
            "capacidad_ahorro": ingreso - gasto,
            "saldo_liquido_disponible": rng.uniform(0, 60000, n),
            "ratio_endeudamiento": (tarjeta + prestamo) * rng.uniform(0, 4, n),
            "uso_linea_credito": tarjeta * rng.uniform(0, 1, n),
            "frecuencia_transacciones": rng.uniform(10, 80, n),
            "actividad_p2p": rng.normal(0, 800, n),
            "edad_anios": rng.uniform(19, 70, n),
            "antiguedad_dias": rng.integers(30, 4000, n),
            "ingreso_declarado": ingreso * rng.uniform(0.8, 1.3, n),
            "numero_productos": 1 + tarjeta + prestamo,
            "tiene_tarjeta_credito": tarjeta,
            "tiene_prestamo_personal": prestamo,
            "proporcion_retiros": rng.uniform(0, 0.4, n),
            "categoria_gasto_dominante": rng.choice(CATEGORIAS, n),
            "fecha_corte": dt.date(2026, 12, 31),
            "fecha_calculo": dt.datetime(2027, 1, 1),
        }
    )
    return df.set_index("cliente_id")


def etiquetas_sinteticas(features: pd.DataFrame, semilla: int = 7) -> pd.Series:
    rng = np.random.default_rng(semilla)
    puntaje = (
        -features["capacidad_ahorro"] / 3000
        + features["ratio_endeudamiento"]
        - features["saldo_liquido_disponible"] / 30000
        + rng.normal(0, 1, len(features))
    )
    return (puntaje > puntaje.median()).astype(int)


def entrenar(features: pd.DataFrame, y: pd.Series) -> XGBClassifier:
    """Modelo chico con la misma preparacion que train_model.py."""
    modelo = XGBClassifier(
        n_estimators=40, max_depth=3, learning_rate=0.2, random_state=42
    )
    modelo.fit(preparar_para_modelo(features), y)
    return modelo


# cliente: (ingresos [(dia, tipo, monto)] por mes, compras al mes, saldo,
#           categoria dominante, creditos [(tipo, saldo, limite, original, plazo)])
def _clientes() -> dict:
    sm = round(SALARIO_MINIMO / 2, 2)
    variable = [
        [(4, "deposito", 3000.0), (20, "deposito", 5000.0)],
        [(18, "deposito", 9000.0)],
        [(2, "deposito", 4000.0), (27, "deposito", 2500.0)],
    ]
    return {
        "CLI-SM": (
            [[(15, "nomina", sm), (30, "nomina", sm)]] * 12,
            8000.0,
            1200.0,
            "comida",
            [],
        ),
        "CLI-20K": (
            [[(15, "nomina", 10000.0), (30, "nomina", 10000.0)]] * 12,
            14000.0,
            25000.0,
            "servicios",
            [
                ("tarjeta_credito", 9000.0, 20000.0, None, None),
                ("prestamo_personal", 50000.0, None, 60000.0, 24),
            ],
        ),
        "CLI-VAR": (
            variable * 4,
            6000.0,
            3000.0,
            "transporte",
            [("prestamo_personal", 20000.0, None, 30000.0, 18)],
        ),
        "CLI-SIN": (
            [[(15, "nomina", 10000.0), (30, "nomina", 10000.0)]] * 12,
            13000.0,
            80000.0,
            "hogar",
            [],
        ),
    }


CLIENTES = list(_clientes())


def crear_gold_vista(tmp_path: Path) -> dict:
    """Gold, modelo y configuracion del dashboard (comun.config) para la vista
    del cliente. Todo dentro de tmp_path."""
    ruta = tmp_path / "kpis.duckdb"
    clientes = _clientes()
    sinteticas = features_sinteticas(150)
    modelo = entrenar(sinteticas, etiquetas_sinteticas(sinteticas))

    movimientos, quincenas, creditos, features = [], [], [], []
    for cliente, (ingresos, compras, saldo, categoria, cuentas) in clientes.items():
        totales = []
        for mes, pagos in zip(MESES, ingresos):
            for dia, tipo, monto in pagos:
                q = 1 if dia <= 15 else 2
                quincenas.append((cliente, mes, q, tipo, monto, 1))
            for tipo in {t for _, t, _ in pagos}:
                total = sum(m for _, t, m in pagos if t == tipo)
                movimientos.append((cliente, mes, tipo, "ingreso", total, len(pagos)))
            movimientos.append((cliente, mes, "compra", categoria, -compras * 0.6, 8))
            movimientos.append((cliente, mes, "compra", "otros", -compras * 0.4, 5))
            if cuentas:
                movimientos.append(
                    (cliente, mes, "pago_recurrente", "pago_deuda", -2500.0, 1)
                )
            totales.append(sum(m for _, _, m in pagos))
        for j, (tipo, s, lim, original, plazo) in enumerate(cuentas):
            creditos.append(
                (
                    cliente,
                    f"{cliente}-{j}",
                    tipo,
                    s,
                    lim,
                    original,
                    plazo,
                    dt.date(2025, 1, 1),
                )
            )
        ingreso = float(np.mean(totales))
        deuda = sum(s for _, s, _, _, _ in cuentas)
        tarjetas = [(s, lim) for t, s, lim, _, _ in cuentas if t == "tarjeta_credito"]
        features.append(
            {
                "cliente_id": cliente,
                "ingreso_mensual_promedio": ingreso,
                "estabilidad_ingreso": float(np.std(totales, ddof=1)),
                "gasto_mensual_total": compras,
                "gasto_promedio_3_meses": compras,
                "capacidad_ahorro": ingreso - compras - (2500.0 if cuentas else 0.0),
                "saldo_liquido_disponible": saldo,
                "ratio_endeudamiento": deuda / ingreso if ingreso else 0.0,
                "uso_linea_credito": (
                    tarjetas[0][0] / tarjetas[0][1] if tarjetas else 0.0
                ),
                "frecuencia_transacciones": 15.0,
                "actividad_p2p": 0.0,
                "categoria_gasto_dominante": categoria,
                "edad_anios": 34.0,
                "antiguedad_dias": 900,
                "ingreso_declarado": ingreso,
                "numero_productos": 1 + len(cuentas),
                "tiene_tarjeta_credito": int(bool(tarjetas)),
                "tiene_prestamo_personal": int(
                    any(t == "prestamo_personal" for t, *_ in cuentas)
                ),
                "proporcion_retiros": 0.05,
                "fecha_corte": dt.date(2026, 12, 31),
                "fecha_calculo": dt.datetime(2027, 1, 1),
            }
        )
    features = pd.DataFrame(features)
    probabilidad = modelo.predict_proba(
        preparar_para_modelo(
            features.set_index("cliente_id"), list(modelo.feature_names_in_)
        )
    )[:, 1]
    kpis = pd.DataFrame(
        {
            "cliente_id": features["cliente_id"],
            "kpi_id": "probabilidad_impago",
            "valor_numerico": probabilidad,
        }
    )
    tablas = {
        "gold_features_cliente": features,
        "gold_kpis": kpis,
        "gold_movimientos_mensuales": pd.DataFrame(
            movimientos,
            columns=[
                "cliente_id",
                "mes",
                "tipo_transaccion",
                "categoria",
                "monto_total",
                "n_movimientos",
            ],
        ),
        "gold_ingresos_quincenales": pd.DataFrame(
            quincenas,
            columns=[
                "cliente_id",
                "mes",
                "quincena",
                "tipo_transaccion",
                "monto_total",
                "n_movimientos",
            ],
        )
        .groupby(["cliente_id", "mes", "quincena", "tipo_transaccion"], as_index=False)
        .sum(),
        "gold_creditos_cliente": pd.DataFrame(
            creditos,
            columns=[
                "cliente_id",
                "cuenta_id",
                "tipo_cuenta",
                "saldo_actual",
                "limite_credito",
                "monto_original",
                "plazo_meses",
                "fecha_apertura",
            ],
        ),
    }
    con = duckdb.connect(str(ruta))
    for nombre, df in tablas.items():
        con.register("df", df)
        con.execute(f"CREATE TABLE {nombre} AS SELECT * FROM df")
        con.unregister("df")
    con.close()

    modelos = tmp_path / "models"
    modelos.mkdir(exist_ok=True)
    joblib.dump(modelo, modelos / "risk_model.joblib")
    for carpeta in ("logs", "deriva_psi", "resumen"):
        (tmp_path / carpeta).mkdir(exist_ok=True)
    return {
        "gold_duckdb": str(ruta),
        "logs": str(tmp_path / "logs"),
        "deriva_psi": str(tmp_path / "deriva_psi"),
        "resumen_portafolio": str(tmp_path / "resumen"),
        "monitoreo_psi": str(RAIZ / "config" / "monitoreo_psi.yaml"),
        "usuarios": str(tmp_path / "auth" / "usuarios.duckdb"),
        "modelo": str(modelos / "risk_model.joblib"),
        "probabilidad_oof": str(modelos / "oof.parquet"),
        "modelos_pliegues": str(modelos / "pliegues.joblib"),
        "bandas_riesgo": [
            {"nombre": "bajo", "desde": 0.0},
            {"nombre": "medio", "desde": 0.10},
            {"nombre": "alto", "desde": 0.30},
        ],
        "ventana_meses": 12,
        "tasa_objetivo": 0.08,
    }

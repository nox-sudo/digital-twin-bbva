"""
pipeline_summary.py

Genera un reporte visual en HTML del estado actual del pipeline:
conteos por entidad en Bronze y Silver, filas en cuarentena, y tiempos
de la ultima corrida. Pensado para demos rapidas donde levantar Docker
completo no es necesario ni conveniente por tiempo.

No requiere servidor: genera un archivo HTML autocontenido que se abre
directo en el navegador.

Uso:
    python pipeline_summary.py --bronze data/bronze --silver data/silver \
        --quarantine data/silver_quarantine --logs data/logs --out reporte_pipeline.html
"""

import argparse
import json
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.spark_session import get_spark_session  # noqa: E402

ENTIDADES = ["clientes", "cuentas", "cetes_inversiones", "transacciones"]


def contar_filas(spark, path: str, entidad: str) -> int:
    try:
        return spark.read.format("delta").load(f"{path}/{entidad}").count()
    except Exception:
        return 0


def cargar_ultimo_log(log_dir: str, prefijo: str) -> dict | None:
    archivos = sorted(Path(log_dir).glob(f"{prefijo}_*.json"))
    if not archivos:
        return None
    with open(archivos[-1]) as f:
        return json.load(f)


def generar_html(datos: dict, out_path: str):
    filas_entidades = ""
    for entidad in ENTIDADES:
        bronze_n = datos["bronze"].get(entidad, 0)
        silver_n = datos["silver"].get(entidad, 0)
        cuarentena_n = datos["cuarentena"].get(entidad, 0)
        estado_color = "#2E7D32" if cuarentena_n == 0 else "#C62828"
        filas_entidades += f"""
        <tr>
            <td class="entidad">{entidad}</td>
            <td class="num">{bronze_n:,}</td>
            <td class="num">{silver_n:,}</td>
            <td class="num" style="color:{estado_color}; font-weight:600;">{cuarentena_n:,}</td>
        </tr>"""

    silver_log = datos.get("silver_log") or {}
    bronze_log = datos.get("bronze_log") or {}

    sin_errores = sum(datos["cuarentena"].values()) == 0
    estado_clase = "estado-ok" if sin_errores else "estado-mal"
    estado_texto = "Sin errores" if sin_errores else "Con cuarentena"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Gemelo Digital Financiero - Estado del Pipeline</title>
<style>
    body {{
        font-family: -apple-system, "Segoe UI", Arial, sans-serif;
        background: #F5F6F8;
        margin: 0;
        padding: 40px;
        color: #1A1A1A;
    }}
    .contenedor {{ max-width: 1000px; margin: 0 auto; }}
    h1 {{ color: #1F3864; margin-bottom: 4px; }}
    .subtitulo {{ color: #666; margin-bottom: 32px; }}

    .flujo {{
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 12px;
        margin-bottom: 40px;
        flex-wrap: wrap;
    }}
    .caja {{
        padding: 20px 28px;
        border-radius: 10px;
        color: white;
        font-weight: 600;
        text-align: center;
        min-width: 140px;
    }}
    .caja .num {{ display: block; font-size: 22px; margin-top: 4px; }}
    .fuentes {{ background: #6B7280; }}
    .bronze {{ background: #C9793B; }}
    .silver {{ background: #8C8F94; }}
    .flecha {{ font-size: 24px; color: #999; }}

    table {{
        width: 100%;
        border-collapse: collapse;
        background: white;
        border-radius: 10px;
        overflow: hidden;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        margin-bottom: 32px;
    }}
    th {{
        background: #1F3864;
        color: white;
        text-align: left;
        padding: 12px 16px;
        font-size: 13px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    td {{ padding: 12px 16px; border-bottom: 1px solid #EEE; }}
    .entidad {{ font-weight: 600; text-transform: capitalize; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}

    .tarjetas {{ display: flex; gap: 16px; margin-bottom: 32px; }}
    .tarjeta {{
        flex: 1;
        background: white;
        border-radius: 10px;
        padding: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }}
    .tarjeta .titulo {{
        font-size: 13px; color: #666; text-transform: uppercase; letter-spacing: 0.5px;
    }}
    .tarjeta .valor {{ font-size: 28px; font-weight: 700; color: #1F3864; margin-top: 6px; }}

    .estado-ok {{ color: #2E7D32; }}
    .estado-mal {{ color: #C62828; }}
    footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="contenedor">
    <h1>Gemelo Digital Financiero</h1>
    <div class="subtitulo">Estado actual del pipeline — Bronze → Silver</div>

    <div class="flujo">
        <div class="caja fuentes">Fuentes<span class="num">sintéticas</span></div>
        <div class="flecha">→</div>
        <div class="caja bronze">
            Bronze<span class="num">{sum(datos['bronze'].values()):,}</span>
        </div>
        <div class="flecha">→</div>
        <div class="caja silver">
            Silver<span class="num">{sum(datos['silver'].values()):,}</span>
        </div>
    </div>

    <div class="tarjetas">
        <div class="tarjeta">
            <div class="titulo">Duración Bronze</div>
            <div class="valor">{bronze_log.get('duracion_segundos', '—')}s</div>
        </div>
        <div class="tarjeta">
            <div class="titulo">Duración Silver</div>
            <div class="valor">{silver_log.get('duracion_total_segundos', '—')}s</div>
        </div>
        <div class="tarjeta">
            <div class="titulo">Estado</div>
            <div class="valor {estado_clase}">
                {estado_texto}
            </div>
        </div>
    </div>

    <table>
        <thead>
            <tr><th>Entidad</th><th>Bronze</th><th>Silver</th><th>Cuarentena</th></tr>
        </thead>
        <tbody>
            {filas_entidades}
        </tbody>
    </table>

    <footer>
        Generado automáticamente por pipeline_summary.py
        — Path Data Engineering, BBVA / Tecmilenio
    </footer>
</div>
</body>
</html>"""

    Path(out_path).write_text(html, encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bronze", default="data/bronze")
    parser.add_argument("--silver", default="data/silver")
    parser.add_argument("--quarantine", default="data/silver_quarantine")
    parser.add_argument("--logs", default="data/logs")
    parser.add_argument("--out", default="reporte_pipeline.html")
    parser.add_argument(
        "--no-abrir", action="store_true", help="No abrir el navegador automaticamente"
    )
    args = parser.parse_args()

    spark = get_spark_session("pipeline_summary")

    datos = {
        "bronze": {e: contar_filas(spark, args.bronze, e) for e in ENTIDADES},
        "silver": {e: contar_filas(spark, args.silver, e) for e in ENTIDADES},
        "cuarentena": {e: contar_filas(spark, args.quarantine, e) for e in ENTIDADES},
        "bronze_log": cargar_ultimo_log(args.logs, "bronze_ingestion"),
        "silver_log": cargar_ultimo_log(args.logs, "silver_run"),
    }

    spark.stop()

    out_path = generar_html(datos, args.out)
    print(f"Reporte generado: {Path(out_path).resolve()}")

    if not args.no_abrir:
        webbrowser.open(f"file://{Path(out_path).resolve()}")


if __name__ == "__main__":
    main()

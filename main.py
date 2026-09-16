"""
main.py

CLI unico del proyecto: expone cada paso del pipeline como subcomando,
delegando al script real (que sigue siendo ejecutable por separado, sin
cambios). Reemplaza tener que recordar el nombre exacto de 7 archivos
distintos por un solo punto de entrada.

Uso:
    python main.py <paso> [opciones]

Pasos disponibles:
    generate       generate_synthetic_sources.py  (fuentes sinteticas)
    bronze         ingest_bronze.py                (ingesta Bronze)
    silver         transform_silver.py             (Bronze -> Silver)
    gold           transform_gold.py               (Silver -> Gold/KPIs)
    labels         generate_labels.py               (etiquetas de riesgo)
    train-model    train_model.py                   (entrena XGBoost)
    predict-risk   predict_risk.py                  (predice y escribe a Gold)

Cada paso reenvia sus opciones tal cual al argparse del script real, por
ejemplo:
    python main.py bronze --source data/raw_sources --out data/bronze
    python main.py train-model --model-out models/risk_model.joblib

Para ver las opciones de un paso especifico:
    python main.py <paso> --help
"""

import sys

# Nombre de modulo por paso, no el objeto importado: el import es
# perezoso (adentro de main()) para que "python main.py bronze" no
# dependa de que XGBoost/SHAP esten instalables en la maquina, aunque
# ese paso ni los use.
PASOS = {
    "generate": "generate_synthetic_sources",
    "bronze": "ingest_bronze",
    "silver": "transform_silver",
    "gold": "transform_gold",
    "labels": "generate_labels",
    "train-model": "train_model",
    "predict-risk": "predict_risk",
}


def _imprimir_ayuda():
    print("Uso: python main.py <paso> [opciones]")
    print(f"Pasos disponibles: {', '.join(PASOS)}")
    print("Para ver las opciones de un paso especifico: python main.py <paso> --help")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in PASOS:
        _imprimir_ayuda()
        sys.exit(0 if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help") else 1)

    paso = sys.argv[1]
    nombre_modulo = PASOS[paso]
    modulo = __import__(nombre_modulo)

    # Reenvia las opciones tal cual al argparse propio del script, solo
    # sacando "main.py" y el nombre del paso de sys.argv.
    sys.argv = [f"{nombre_modulo}.py"] + sys.argv[2:]
    modulo.main()


if __name__ == "__main__":
    main()

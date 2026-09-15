# Deuda tecnica conocida

Registro de gaps conocidos que no bloquean el estado actual del pipeline,
pero que un futuro colaborador (o evaluador) deberia poder encontrar aca,
sin tener que preguntar directamente.

Ultima verificacion: 2026-09-15 (PR #8)

## CI: el smoke test no cubre el pipeline del modelo de riesgo end-to-end

**Que falta:** `.github/workflows/ci.yml` (job `pipeline-smoke-test`) corre
`generate_synthetic_sources.py` -> `ingest_bronze.py` -> `transform_silver.py`
-> `transform_gold.py`, y verifica que Gold tenga los 12 KPIs esperados. No
incluye `generate_labels.py` -> `train_model.py` -> `predict_risk.py`.

**Que si existe:** `tests/test_model.py` prueba el contrato de carga y
prediccion del modelo (`joblib.dump`/`load`, rango valido de
`predict_proba`) con un modelo minimo entrenado en memoria sobre datos
sinteticos generados en el propio test. No ejercita el feature engineering
real (`src/gold/risk_features.py`) ni el entrenamiento contra datos
generados por el pipeline.

**Por que no bloquea:** el pipeline del modelo se verifico manualmente
end-to-end en Docker (imagen `gemelo-worker:local`, con `libgomp1`) antes
de mergear el PR #8 - resultado reproducible, sin perdida de filas en
Gold (5602 filas antes y despues, `probabilidad_impago` 500/500 con valor
real). Esa verificacion fue manual, no automatica: un cambio futuro en
`risk_features.py` o `kpi_definitions.py` podria romper el pipeline del
modelo sin que CI lo detecte.

## Posible fix

Agregar un job (o extender `pipeline-smoke-test`) que corra
`generate_labels.py` -> `train_model.py` -> `predict_risk.py` con el
volumen reducido del smoke test, y verifique que `probabilidad_impago` en
`gold_kpis` deja de tener `NULL`.

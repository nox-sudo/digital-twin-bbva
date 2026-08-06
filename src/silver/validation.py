"""
src/silver/validation.py

Motor generico de validacion de calidad de datos. No sabe nada de
"clientes" ni "transacciones": solo sabe leer un diccionario de reglas
(cargado desde config/business_rules.yaml) y aplicarlo a un DataFrame.

Esto es lo que hace que agregar una entidad nueva, o una regla nueva a
una entidad existente, no requiera escribir codigo de validacion nuevo:
solo se declara en el YAML.

Patron de cuarentena: las filas que violan alguna regla no se descartan
en silencio, se separan a un DataFrame de cuarentena con la razon de
por que fueron rechazadas, tal como lo describe el documento de
propuesta del proyecto (aislamiento de datos corruptos, sin detener el
flujo principal).
"""

from dataclasses import dataclass, field

from pyspark.sql import DataFrame, functions as F


class SchemaValidationError(Exception):
    """
    Se lanza cuando una entidad no tiene las columnas que sus reglas de
    negocio esperan. Esto ocurre ANTES de aplicar cualquier regla, para
    fallar con un mensaje claro y accionable en vez de que Spark lance
    un AnalysisException critico durante la construccion del plan de
    ejecucion cuando finalmente intenta resolver una columna inexistente.

    Se distingue deliberadamente de una violacion de regla de negocio
    (datos invalidos, que van a cuarentena): una columna faltante es un
    problema estructural, no un problema de calidad de un valor
    individual, y no se puede evaluar fila por fila.
    """

    def __init__(
        self,
        entidad: str,
        columnas_faltantes: list[str],
        columnas_disponibles: list[str],
    ):
        self.entidad = entidad
        self.columnas_faltantes = columnas_faltantes
        self.columnas_disponibles = columnas_disponibles
        mensaje = (
            f"[{entidad}] La fuente no tiene las columnas que las reglas de negocio "
            f"esperan: {columnas_faltantes}. "
            f"Columnas disponibles en la fuente: {columnas_disponibles}. "
            f"Revisa config/business_rules.yaml contra el esquema real de la entidad "
            f"antes de reintentar — este no es un problema de calidad de datos, "
            f"es un problema de esquema."
        )
        super().__init__(mensaje)


def _required_columns_from_rules(rules: dict) -> set[str]:
    """Recolecta todas las columnas que las reglas declaradas esperan
    encontrar en el DataFrame, sin importar el tipo de regla."""
    columnas: set[str] = set()

    columnas.update(rules.get("not_null", []))
    columnas.update(rules.get("unique", []))
    columnas.update(rules.get("allowed_values", {}).keys())
    columnas.update(rules.get("numeric_ranges", {}).keys())
    columnas.update(rules.get("numeric_not_zero", []))
    columnas.update(rules.get("date_not_future", []))
    columnas.update(rules.get("foreign_keys", {}).keys())

    for regla in rules.get("conditional_not_null", []):
        columnas.add(regla["column"])
        columnas.add(regla["when_column"])

    return columnas


def _validar_esquema(df: DataFrame, entity_name: str, rules: dict) -> None:
    """Verifica que todas las columnas que las reglas esperan existan
    en el DataFrame. Lanza SchemaValidationError si falta alguna,
    ANTES de intentar aplicar ninguna regla."""
    esperadas = _required_columns_from_rules(rules)
    disponibles = set(df.columns)
    faltantes = sorted(esperadas - disponibles)

    if faltantes:
        raise SchemaValidationError(
            entidad=entity_name,
            columnas_faltantes=faltantes,
            columnas_disponibles=sorted(disponibles),
        )


@dataclass
class ValidationReport:
    """Resultado de validar una entidad: conteos por regla violada."""

    entidad: str
    filas_entrada: int
    filas_validas: int
    filas_cuarentena: int
    violaciones_por_regla: dict = field(default_factory=dict)


def _check_not_null(df: DataFrame, columns: list[str]) -> DataFrame:
    for col in columns:
        df = df.withColumn(f"_viol_not_null_{col}", F.col(col).isNull())
    return df


def _check_unique(df: DataFrame, columns: list[str]) -> DataFrame:
    for col in columns:
        window_counts = (
            df.groupBy(col).count().withColumnRenamed("count", f"_cnt_{col}")
        )
        df = df.join(window_counts, on=col, how="left")
        df = df.withColumn(f"_viol_unique_{col}", F.col(f"_cnt_{col}") > 1)
        df = df.drop(f"_cnt_{col}")
    return df


def _check_allowed_values(df: DataFrame, rules: dict) -> DataFrame:
    for col, allowed in rules.items():
        df = df.withColumn(f"_viol_allowed_{col}", ~F.col(col).isin(allowed))
    return df


def _check_numeric_ranges(df: DataFrame, rules: dict) -> DataFrame:
    for col, bounds in rules.items():
        condition = F.lit(False)
        if "min" in bounds:
            condition = condition | (F.col(col) < bounds["min"])
        if "max" in bounds:
            condition = condition | (F.col(col) > bounds["max"])
        df = df.withColumn(f"_viol_range_{col}", condition)
    return df


def _check_numeric_not_zero(df: DataFrame, columns: list[str]) -> DataFrame:
    for col in columns:
        df = df.withColumn(f"_viol_notzero_{col}", F.col(col) == 0)
    return df


def _check_date_not_future(df: DataFrame, columns: list[str]) -> DataFrame:
    for col in columns:
        df = df.withColumn(
            f"_viol_futuredate_{col}",
            F.to_date(F.col(col)) > F.current_date(),
        )
    return df


def _check_conditional_not_null(df: DataFrame, rules: list[dict]) -> DataFrame:
    for i, rule in enumerate(rules):
        col = rule["column"]
        when_col = rule["when_column"]
        when_val = rule["when_equals"]
        df = df.withColumn(
            f"_viol_condnull_{col}_{i}",
            (F.col(when_col) == when_val) & F.col(col).isNull(),
        )
    return df


def _check_foreign_keys(df: DataFrame, rules: dict, silver_context: dict) -> DataFrame:
    for col, fk in rules.items():
        ref_entity = fk["references_entity"]
        ref_col = fk["references_column"]
        if ref_entity not in silver_context:
            raise ValueError(
                f"foreign_key hacia '{ref_entity}' pero esa entidad aun no fue "
                f"procesada. Revisa el orden en transform_silver.py."
            )
        ref_ids = silver_context[ref_entity].select(ref_col).distinct()
        ref_ids = ref_ids.withColumnRenamed(ref_col, f"_ref_{col}")
        df = df.join(ref_ids, df[col] == F.col(f"_ref_{col}"), how="left")
        df = df.withColumn(f"_viol_fk_{col}", F.col(f"_ref_{col}").isNull())
        df = df.drop(f"_ref_{col}")
    return df


def validate_entity(
    df: DataFrame,
    entity_name: str,
    rules: dict,
    silver_context: dict | None = None,
) -> tuple[DataFrame, DataFrame, ValidationReport]:
    """Aplica todas las reglas declaradas para una entidad.

    Retorna (df_valido, df_cuarentena, reporte). Las columnas de trabajo
    (prefijo _viol_) no aparecen en ninguno de los dos DataFrames de
    salida, solo se usan internamente.

    Lanza SchemaValidationError si a la entidad le falta alguna columna
    que las reglas esperan — se verifica antes de tocar cualquier dato,
    para fallar rapido y con un mensaje claro.
    """
    _validar_esquema(df, entity_name, rules)

    silver_context = silver_context or {}
    filas_entrada = df.count()
    df = df.withColumn("_row_id_tmp", F.monotonically_increasing_id())

    if "not_null" in rules:
        df = _check_not_null(df, rules["not_null"])
    if "unique" in rules:
        df = _check_unique(df, rules["unique"])
    if "allowed_values" in rules:
        df = _check_allowed_values(df, rules["allowed_values"])
    if "numeric_ranges" in rules:
        df = _check_numeric_ranges(df, rules["numeric_ranges"])
    if "numeric_not_zero" in rules:
        df = _check_numeric_not_zero(df, rules["numeric_not_zero"])
    if "date_not_future" in rules:
        df = _check_date_not_future(df, rules["date_not_future"])
    if "conditional_not_null" in rules:
        df = _check_conditional_not_null(df, rules["conditional_not_null"])
    if "foreign_keys" in rules:
        df = _check_foreign_keys(df, rules["foreign_keys"], silver_context)

    viol_cols = [c for c in df.columns if c.startswith("_viol_")]

    violaciones_por_regla = {}
    if viol_cols:
        counts_row = df.select(
            [F.sum(F.col(c).cast("int")).alias(c) for c in viol_cols]
        ).collect()[0]
        violaciones_por_regla = {
            c.replace("_viol_", ""): (counts_row[c] or 0) for c in viol_cols
        }
        violaciones_por_regla = {
            k: v for k, v in violaciones_por_regla.items() if v > 0
        }

    if viol_cols:
        es_invalido = F.lit(False)
        for c in viol_cols:
            es_invalido = es_invalido | F.col(c)
        df = df.withColumn("_es_invalido", es_invalido)

        motivo_partes = [
            F.when(F.col(c), F.lit(c.replace("_viol_", "") + "; ")).otherwise(F.lit(""))
            for c in viol_cols
        ]
        df = df.withColumn("_motivo_cuarentena", F.concat(*motivo_partes))
    else:
        df = df.withColumn("_es_invalido", F.lit(False))
        df = df.withColumn("_motivo_cuarentena", F.lit(""))

    df_valido = df.filter(~F.col("_es_invalido")).drop(
        "_row_id_tmp", "_es_invalido", "_motivo_cuarentena", *viol_cols
    )
    df_cuarentena = df.filter(F.col("_es_invalido")).select(
        "_row_id_tmp",
        "_motivo_cuarentena",
        *[
            c
            for c in df.columns
            if not c.startswith("_viol_")
            and c not in ("_row_id_tmp", "_es_invalido", "_motivo_cuarentena")
        ],
    )

    filas_validas = df_valido.count()
    filas_cuarentena = df_cuarentena.count()

    reporte = ValidationReport(
        entidad=entity_name,
        filas_entrada=filas_entrada,
        filas_validas=filas_validas,
        filas_cuarentena=filas_cuarentena,
        violaciones_por_regla=violaciones_por_regla,
    )
    return df_valido, df_cuarentena, reporte

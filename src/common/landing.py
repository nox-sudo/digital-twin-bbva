"""
src/common/landing.py

Zona de aterrizaje (landing zone) en object storage S3-compatible
(MinIO en local; el mismo codigo funcionaria contra AWS S3, porque
habla el mismo protocolo a traves de boto3).

En un banco, la landing zone es donde los sistemas de origen depositan
lo que envian, tal cual, antes de que el pipeline lo procese. Es el
historico inmutable de lo que realmente llego: si Bronze se pierde o se
corrompe, se reconstruye desde aqui.

Estructura en el bucket:

    landing/entregas/<entrega_id>/manifest.json      se escribe al final
    landing/entregas/<entrega_id>/clientes.csv
    landing/entregas/<entrega_id>/transacciones/transacciones_2026_10.csv
    ...

Una entrega es una sesion de datos (src/common/sesiones.py) publicada.
Solo se suben los archivos que no llegaron identicos en una entrega
anterior: si la entrega de octubre trae el mismo clientes.csv que la de
septiembre, no se vuelve a subir; trae solo el mes nuevo de
transacciones. Eso es lo que permite que Bronze ingiera de forma
incremental.

Garantias:
- Inmutabilidad: una entrega publicada no se vuelve a escribir.
- Completitud: el manifest se sube al final. Una entrega sin manifest
  (publicacion interrumpida) no existe para quien lee.
- Integridad: cada objeto lleva su SHA-256 en los metadatos, y la
  descarga lo verifica. Un archivo alterado en el bucket se detecta
  antes de llegar a Bronze.
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

PREFIJO_ENTREGAS = "landing/entregas"
BUCKET_DEFAULT = "gemelo-digital-financiero"
NOMBRE_MANIFEST = "manifest.json"


def cliente_s3():
    """Cliente S3 configurado desde el entorno: MINIO_ENDPOINT,
    MINIO_ROOT_USER, MINIO_ROOT_PASSWORD (inyectados por compose o el
    DAG; las credenciales salen de .env)."""
    import boto3
    from botocore.config import Config

    from src.common.secretos import obtener_secreto

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("MINIO_ENDPOINT", "http://localhost:9000"),
        aws_access_key_id=obtener_secreto("MINIO_ROOT_USER"),
        aws_secret_access_key=obtener_secreto("MINIO_ROOT_PASSWORD"),
        region_name="us-east-1",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5}),
    )


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _leer_json(s3, bucket: str, llave: str) -> dict:
    cuerpo = s3.get_object(Bucket=bucket, Key=llave)["Body"].read()
    return json.loads(cuerpo)


def listar_entregas(s3, bucket: str = BUCKET_DEFAULT) -> list[dict]:
    """Manifests de todas las entregas completas, en orden de publicacion."""
    manifests = []
    paginador = s3.get_paginator("list_objects_v2")
    for pagina in paginador.paginate(Bucket=bucket, Prefix=f"{PREFIJO_ENTREGAS}/"):
        for objeto in pagina.get("Contents", []):
            if objeto["Key"].endswith(f"/{NOMBRE_MANIFEST}"):
                manifests.append(_leer_json(s3, bucket, objeto["Key"]))
    return sorted(manifests, key=lambda m: (m["publicada_en"], m["entrega_id"]))


def publicar_entrega(
    s3, carpeta_sesion: Path, bucket: str = BUCKET_DEFAULT
) -> dict | None:
    """Publica una sesion como entrega. Devuelve el manifest de la
    entrega, o None si no habia nada nuevo que publicar."""
    with open(carpeta_sesion / NOMBRE_MANIFEST, encoding="utf-8") as f:
        sesion = json.load(f)
    entrega_id = sesion["sesion_id"]

    anteriores = listar_entregas(s3, bucket)
    if any(m["entrega_id"] == entrega_id for m in anteriores):
        print(f"[Landing] La entrega {entrega_id} ya estaba publicada; no se modifica.")
        return None

    ya_recibidos = {
        (ruta, sha) for m in anteriores for ruta, sha in m["archivos"].items()
    }
    nuevos = {
        ruta: sha
        for ruta, sha in sesion["archivos"].items()
        if (ruta, sha) not in ya_recibidos
    }
    if not nuevos:
        print(
            f"[Landing] {entrega_id}: todos sus archivos ya llegaron en entregas previas."
        )
        return None

    prefijo = f"{PREFIJO_ENTREGAS}/{entrega_id}"
    for ruta, sha in sorted(nuevos.items()):
        local = carpeta_sesion / ruta
        if _sha256(local) != sha:
            raise RuntimeError(
                f"{local} no coincide con el checksum de su sesion; no se publica."
            )
        s3.upload_file(
            str(local),
            bucket,
            f"{prefijo}/{ruta}",
            ExtraArgs={"Metadata": {"sha256": sha}},
        )
        print(f"[Landing] Subido {ruta}")

    manifest = {
        "entrega_id": entrega_id,
        "publicada_en": datetime.now().isoformat(timespec="microseconds"),
        "parametros_sesion": sesion["parametros"],
        "archivos": nuevos,
    }
    s3.put_object(
        Bucket=bucket,
        Key=f"{prefijo}/{NOMBRE_MANIFEST}",
        Body=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"[Landing] Entrega {entrega_id} publicada: {len(nuevos)} archivos nuevos.")
    return manifest


def descargar_entrega(
    s3, manifest: dict, destino: Path, bucket: str = BUCKET_DEFAULT
) -> Path:
    """Descarga los archivos de una entrega a destino/<entrega_id>/,
    verificando el SHA-256 de cada uno contra el manifest."""
    carpeta = destino / manifest["entrega_id"]
    prefijo = f"{PREFIJO_ENTREGAS}/{manifest['entrega_id']}"
    for ruta, sha in manifest["archivos"].items():
        local = carpeta / ruta
        local.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, f"{prefijo}/{ruta}", str(local))
        if _sha256(local) != sha:
            raise RuntimeError(
                f"Integridad: {prefijo}/{ruta} no coincide con su checksum. "
                "El objeto fue alterado en la landing zone; no se ingiere."
            )
    with open(carpeta / NOMBRE_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return carpeta

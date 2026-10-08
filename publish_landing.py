"""
publish_landing.py

Publica la sesion de datos activa como una entrega en la zona de
aterrizaje (MinIO). Solo sube lo que no llego identico en una entrega
anterior; una entrega ya publicada no se modifica. Detalle en
src/common/landing.py.

Uso:
    python publish_landing.py --sesion data/raw_sources

Requiere MINIO_ENDPOINT, MINIO_ROOT_USER y MINIO_ROOT_PASSWORD (variables
de entorno o .env).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.common.landing import (  # noqa: E402
    BUCKET_DEFAULT,
    cliente_s3,
    listar_entregas,
    publicar_entrega,
)


def main():
    parser = argparse.ArgumentParser(
        description="Publica la sesion activa en la landing zone"
    )
    parser.add_argument("--sesion", default="data/raw_sources")
    parser.add_argument("--bucket", default=BUCKET_DEFAULT)
    args = parser.parse_args()

    s3 = cliente_s3()
    publicar_entrega(s3, Path(args.sesion), args.bucket)

    entregas = listar_entregas(s3, args.bucket)
    print(f"\nEntregas en la landing zone ({len(entregas)}):")
    for m in entregas:
        print(
            f"  {m['publicada_en']}  {m['entrega_id']}  ({len(m['archivos'])} archivos)"
        )


if __name__ == "__main__":
    main()

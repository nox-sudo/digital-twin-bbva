"""
src/common/logging_utils.py

Registro estructurado de corridas del pipeline, reusable entre Bronze,
Silver y Gold. Escribe un log JSON por corrida con metricas por entidad,
no solo el tiempo total (limitacion que tenia el log original de Bronze).
"""

import json
from datetime import datetime
from pathlib import Path


class PipelineRunLogger:
    """Acumula metricas por entidad durante una corrida y las persiste.

    Uso:
        logger = PipelineRunLogger(layer="silver", log_dir="data/logs")
        logger.start_entity("clientes")
        ... procesar clientes ...
        logger.end_entity("clientes", filas_entrada=500, filas_salida=498,
                           filas_cuarentena=2, extra={"regla_mas_violada": "not_null"})
        logger.write()
    """

    def __init__(self, layer: str, log_dir: str):
        self.layer = layer
        self.log_dir = Path(log_dir)
        self.run_start = datetime.now()
        self.entities: dict[str, dict] = {}
        self._entity_start_times: dict[str, datetime] = {}

    def start_entity(self, entity_name: str) -> None:
        self._entity_start_times[entity_name] = datetime.now()

    def end_entity(self, entity_name: str, **metrics) -> None:
        start = self._entity_start_times.get(entity_name, datetime.now())
        duration = (datetime.now() - start).total_seconds()
        self.entities[entity_name] = {
            "duracion_segundos": round(duration, 3),
            **metrics,
        }
        resumen = ", ".join(f"{k}={v}" for k, v in metrics.items())
        print(f"[{self.layer}:{entity_name}] {resumen} duracion={duration:.2f}s")

    def write(self, status: str = "SUCCESS") -> Path:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        run_end = datetime.now()
        payload = {
            "layer": self.layer,
            "inicio": self.run_start.isoformat(),
            "fin": run_end.isoformat(),
            "duracion_total_segundos": round((run_end - self.run_start).total_seconds(), 3),
            "status": status,
            "entities": self.entities,
        }
        out_path = self.log_dir / f"{self.layer}_run_{self.run_start.strftime('%Y%m%d_%H%M%S')}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"[log] resumen de corrida escrito en {out_path}")
        return out_path

"""
tests/test_silver_rollback.py

Logica de reversion de una corrida fallida de Silver
(transform_silver.restaurar_silver). Usa un DeltaTable falso: la
reversion real con Delta la verifica el smoke test de CI, que corre
Silver sin la entidad transacciones y comprueba que las demas tablas
regresan a su version anterior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import transform_silver  # noqa: E402


class _TablaFalsa:
    """Imita lo que restaurar_silver usa de DeltaTable: la version
    vigente (history(1)) y restoreToVersion."""

    def __init__(self, registro: dict, ruta: str):
        self.registro = registro
        self.ruta = ruta

    def history(self, _limite):
        version = self.registro["vigente"][self.ruta]

        class _Fila:
            def select(self, _col):
                return self

            def first(self):
                return [version]

        return _Fila()

    def restoreToVersion(self, version):
        self.registro["restauradas"][self.ruta] = version


def test_restaura_las_escritas_y_borra_las_nuevas(tmp_path, monkeypatch):
    silver = str(tmp_path)
    registro = {
        "vigente": {f"{silver}/clientes": 5, f"{silver}/cuentas": 3},
        "restauradas": {},
    }
    monkeypatch.setattr(
        transform_silver.DeltaTable,
        "forPath",
        lambda _spark, ruta: _TablaFalsa(registro, ruta),
    )
    # cetes_inversiones no existia antes de la corrida fallida
    (tmp_path / "cetes_inversiones").mkdir()
    versiones_previas = {"clientes": 4, "cuentas": 3, "cetes_inversiones": None}

    transform_silver.restaurar_silver(
        None,
        silver,
        versiones_previas,
        ["clientes", "cuentas", "cetes_inversiones"],
    )

    # clientes avanzo de 4 a 5: se restaura a 4
    assert registro["restauradas"] == {f"{silver}/clientes": 4}
    # cuentas sigue en 3 (fallo antes de escribirla): no se toca
    # cetes_inversiones era nueva: se elimina
    assert not (tmp_path / "cetes_inversiones").exists()

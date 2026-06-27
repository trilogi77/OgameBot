"""Self-check para _aggregate_ships_in_motion: las expediciones en vuelo deben sumarse.

Reproduce el bug reportado: 3 expediciones con ~2018 cargueros grandes cada una salían
del inventario "en vuelo" como 0. Aquí verificamos que, con el desglose por nave presente
(lo que ahora trae la página de movimientos), se suman, se deduplican filas repetidas del
DOM y se ignoran los ataques entrantes.

    python scratch/test_ships_in_motion.py
"""
import logging
import types
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain


def _loc(g, s, p, moon=None):
    return types.SimpleNamespace(
        coords=types.SimpleNamespace(galaxy=g, system=s, position=p), moon=moon
    )


def test_expedition_cargos_counted():
    stub = types.SimpleNamespace(log=logging.getLogger("test"))
    planets = [_loc(3, 202, 8)]
    movements = [
        # 3 expediciones salientes desde el mismo planeta, distinto destino/llegada
        {"origin": "3:202:8", "destination": "3:200:16", "mission": "15",
         "arrival_text": "10m", "is_return": False, "ships": {"Nave grande de carga": 2018}},
        {"origin": "3:202:8", "destination": "3:205:16", "mission": "15",
         "arrival_text": "12m", "is_return": False, "ships": {"Nave grande de carga": 2018}},
        {"origin": "3:202:8", "destination": "3:199:16", "mission": "15",
         "arrival_text": "13m", "is_return": False, "ships": {"Nave grande de carga": 2018}},
        # fila duplicada del DOM (mismo origen/destino/misión/llegada) -> no cuenta dos veces
        {"origin": "3:202:8", "destination": "3:200:16", "mission": "15",
         "arrival_text": "10m", "is_return": False, "ships": {"Nave grande de carga": 2018}},
        # ataque entrante hostil -> se ignora, no es flota nuestra
        {"origin": "1:1:1", "destination": "3:202:8", "mission": "1", "arrival_text": "5m",
         "is_return": False, "is_hostile": True, "ships": {"Nave grande de carga": 9999}},
    ]
    totals = Brain._aggregate_ships_in_motion(stub, movements, planets)
    assert totals.get("large_cargo") == 3 * 2018, totals


if __name__ == "__main__":
    test_expedition_cargos_counted()
    print("OK")

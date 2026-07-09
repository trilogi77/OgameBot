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
from ogbot.brain import Brain, build_flights, _retain_unlanded


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


def test_inventory_retains_when_current_read_lacks_ships():
    """Bug reportado: la tabla de vuelos mostraba las naves pero el inventario decía
    "Actual: 1". Causa: el inventario se sumaba del mvs crudo del ciclo (sin retención); si
    esa lectura no traía el desglose por nave, daba 0 aunque el vuelo siguiera en curso.
    El fix suma del vuelo YA construido (composición retenida). Aquí: ciclo 1 lee la
    composición, ciclo 2 la pierde -> el inventario debe SEGUIR contándola."""
    stub = types.SimpleNamespace(log=logging.getLogger("test"))
    planets = [_loc(2, 113, 10)]

    # Ciclo 1: la página de movimientos SÍ trae el desglose por nave.
    full = [{"origin": "2:113:10", "destination": "2:117:16", "mission": "15",
             "arrival_text": "01:00:00", "arrival_epoch": 1000, "is_return": False,
             "ships": {"Nave pequeña de carga": 124, "Sonda de espionaje": 1}}]
    f1 = _retain_unlanded(build_flights(full, now=0.0, prev=[]), [], 0.0)

    # Ciclo 2: MISMA flota en vuelo pero la lectura NO trae naves (tooltip vacío).
    empty = [{"origin": "2:113:10", "destination": "2:117:16", "mission": "15",
              "arrival_text": "00:59:00", "arrival_epoch": 1000, "is_return": False,
              "ships": {}}]
    f2 = _retain_unlanded(build_flights(empty, now=60.0, prev=f1), f1, 60.0)

    totals = Brain._aggregate_ships_in_motion(stub, f2, planets)
    assert totals.get("small_cargo") == 124, totals   # antes del fix: 0
    assert totals.get("espionage_probe") == 1, totals


if __name__ == "__main__":
    test_expedition_cargos_counted()
    test_inventory_retains_when_current_read_lacks_ships()
    print("OK")

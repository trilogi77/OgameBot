"""Expediciones auto: si faltan NGC para el objetivo, completar la capacidad
restante con NPG (5 NPG por NGC). Ver brain._auto_exp_ships."""
import os, sys
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain

B = brain.Brain
FAKE = SimpleNamespace(cfg=SimpleNamespace(expedition_destroyer_count=0,
                                           expedition_send_probe=False))


def ships(avail, optimal=50, spread=None, use_pf=False, min_cargo=1, ratio=5):
    return B._auto_exp_ships(FAKE, "large_cargo", optimal, optimal * 2, spread,
                             use_pf, avail, min_cargo, ratio)


# NGC de sobra: solo NGC, sin NPG
assert ships({"large_cargo": 100, "small_cargo": 300}) == {"large_cargo": 50}

# NGC cortas: completa con NPG a razon de 5 por NGC que falta
assert ships({"large_cargo": 20, "small_cargo": 300}) == {"large_cargo": 20, "small_cargo": 150}

# NPG tampoco llegan: manda todas las que hay
assert ships({"large_cargo": 20, "small_cargo": 40}) == {"large_cargo": 20, "small_cargo": 40}

# Sin NGC: todo en NPG
assert ships({"small_cargo": 80}) == {"small_cargo": 80}

# Nada que mandar
assert ships({}) is None

# min_cargo cuenta el total mixto
assert ships({"large_cargo": 1, "small_cargo": 1}, min_cargo=3) is None
assert ships({"large_cargo": 1, "small_cargo": 2}, min_cargo=3) == {"large_cargo": 1, "small_cargo": 2}

# spread_cap limita el objetivo (en NGC-equivalentes) tambien para el completado
assert ships({"large_cargo": 4, "small_cargo": 999}, spread=10) == {"large_cargo": 4, "small_cargo": 30}

# Con pathfinder: objetivo doble y se anyade el pathfinder
assert ships({"large_cargo": 30, "small_cargo": 400, "pathfinder": 1}, use_pf=True) == \
    {"large_cargo": 30, "small_cargo": 350, "pathfinder": 1}

# NPG como carguero principal (ratio 0): comportamiento de siempre
assert B._auto_exp_ships(FAKE, "small_cargo", 50, 100, None, False,
                         {"small_cargo": 20}, 1, 0) == {"small_cargo": 20}

print("OK")

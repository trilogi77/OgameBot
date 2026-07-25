"""Bonus de formas de vida en el dimensionado de expediciones.
Compresor neuromodal (+% carga) => menos cargueros; Sensores mejorados
(+% botín) => más cargueros. Ver gamedata.effective_cargo/expedition_max_find_units
y fleet.optimal_expedition_cargo."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import gamedata as gd
from ogbot import fleet

# effective_cargo: el bonus de carga multiplica sobre el de hiperespacio.
base = gd.effective_cargo("large_cargo", 0, 0.0)
assert gd.effective_cargo("large_cargo", 0, 0.06) == int(base * 1.06)
# retrocompat: sin bonus, idéntico al valor histórico (solo hiperespacio).
assert gd.effective_cargo("large_cargo", 10, 0.0) == gd.effective_cargo("large_cargo", 10)

# max_find_units: el bonus de botín sube el tope a dimensionar.
f0 = gd.expedition_max_find_units(1_000_000, 1.0)
assert gd.expedition_max_find_units(1_000_000, 1.0, lf_find_bonus=0.10) == int(f0 * 1.10)

# optimal_expedition_cargo: +carga baja (o iguala) el nº de naves; +botín lo sube.
n0 = fleet.optimal_expedition_cargo(f0, "large_cargo", 1.0, 0)
n_cargo = fleet.optimal_expedition_cargo(f0, "large_cargo", 1.0, 0, lf_cargo_bonus=0.30)
n_find = fleet.optimal_expedition_cargo(
    gd.expedition_max_find_units(1_000_000, 1.0, lf_find_bonus=0.50), "large_cargo", 1.0, 0)
assert n_cargo <= n0, (n_cargo, n0)
assert n_find > n0, (n_find, n0)

# la capacidad total con +carga alcanza el botín (no se pierde loot).
assert n_cargo * gd.effective_cargo("large_cargo", 0, 0.30) >= f0

print("OK", {"base_ngc": base, "n0": n0, "n_cargo": n_cargo, "n_find": n_find})

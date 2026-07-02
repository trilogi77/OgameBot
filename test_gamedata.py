"""Tests sin frameworks: python test_gamedata.py"""
from ogbot import gamedata as gd
from ogbot.moons import moon_chance

# flight_time: universe_fleet_speed=4 debe dar exactamente 1/4 del tiempo con speed=1
dist = gd.distance((1, 1, 1), (1, 50, 8))
t1 = gd.flight_time(dist, 5000, 1.0, universe_fleet_speed=1)
t4 = gd.flight_time(dist, 5000, 1.0, universe_fleet_speed=4)
assert t4 == t1 / 4, f"flight_time x4: esperado {t1 / 4}, obtenido {t4}"

# ion_cannon: coste real 2000 metal / 6000 cristal
ion = gd.DEFENSES["ion_cannon"].cost
assert (ion.metal, ion.crystal) == (2000, 6000), f"ion_cannon: {(ion.metal, ion.crystal)}"

# moon_chance: 1% por cada 100k de escombros, cap 20%
assert moon_chance(100_000) == 0.01, f"moon_chance(100k): {moon_chance(100_000)}"
assert moon_chance(2_000_000) == 0.20, f"moon_chance(2M): {moon_chance(2_000_000)}"
assert moon_chance(50_000_000) == 0.20, f"moon_chance(50M): {moon_chance(50_000_000)}"

# effective_cargo: hiperespacio 10 = base * 1.5
for ship in ("small_cargo", "large_cargo"):
    base = gd.SHIPS[ship].cargo
    assert gd.effective_cargo(ship, 10) == int(base * 1.5), f"effective_cargo({ship})"

# fusion_deut_consumption: 0 a nivel 0 y creciente con el nivel
assert gd.fusion_deut_consumption(0) == 0.0
prev = 0.0
for lvl in range(1, 21):
    cur = gd.fusion_deut_consumption(lvl)
    assert cur > prev, f"fusion_deut_consumption no crece en nivel {lvl}"
    prev = cur

# distance: misma galaxia y sistema, posiciones 1 y 3 -> 1000 + 5*2 = 1010
assert gd.distance((1, 1, 1), (1, 1, 3)) == 1010, gd.distance((1, 1, 1), (1, 1, 3))

print("OK")

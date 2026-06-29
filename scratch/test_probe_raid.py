import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import fleet, targets, gamedata as gd
from ogbot.models import Resources

# Servidor con bodega en sondas (p.ej. 5 u/sonda).
gd.SHIPS["espionage_probe"].cargo = 5

# cargo_capacity ahora cuenta las sondas como carguero.
assert targets.cargo_capacity({"espionage_probe": 20}) == 100, targets.cargo_capacity({"espionage_probe": 20})

# Dimensionado: botín 100 / 5 = 20 sondas.
p = types.SimpleNamespace(ships={"espionage_probe": 1000})
f = fleet.size_attack_fleet_probes(p, Resources(40, 30, 30), {"espionage_probe": 1}, 5)
assert f == {"espionage_probe": 20}, f

# Limitado por sondas disponibles (envío parcial).
p2 = types.SimpleNamespace(ships={"espionage_probe": 6})
f2 = fleet.size_attack_fleet_probes(p2, Resources(1000, 0, 0), {"espionage_probe": 1}, 5)
assert f2 == {"espionage_probe": 6}, f2

# Sondas SOLAS: se ignoran escoltas del template. Sin sondas -> {}.
p3 = types.SimpleNamespace(ships={"espionage_probe": 0, "light_fighter": 50})
f3 = fleet.size_attack_fleet_probes(p3, Resources(100, 0, 0), {"espionage_probe": 1, "light_fighter": 10}, 5)
assert f3 == {}, f3

# Con escoltas en el template y sondas disponibles: van SOLO sondas.
p5 = types.SimpleNamespace(ships={"espionage_probe": 1000, "light_fighter": 50})
f5 = fleet.size_attack_fleet_probes(p5, Resources(40, 30, 30), {"espionage_probe": 1, "light_fighter": 10}, 5)
assert f5 == {"espionage_probe": 20}, f5

# Botín 0 -> al menos 1 sonda.
f4 = fleet.size_attack_fleet_probes(p, Resources(0, 0, 0), {"espionage_probe": 1}, 5)
assert f4 == {"espionage_probe": 1}, f4

print("OK")

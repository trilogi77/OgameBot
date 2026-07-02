"""Self-check de auto_military_escort (auto-flota de farmeo)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.fleet import auto_military_escort
from ogbot.combat import Tech

t = Tech()

# Objetivo indefenso -> sin escolta (raid solo con cargueros)
assert auto_military_escort({"cruiser": 100}, {}, {}, t, t) == {}

# 20 lanzamisiles: bastan unos pocos cruceros, no los 500 del hangar
esc = auto_military_escort({"cruiser": 500}, {}, {"rocket_launcher": 20}, t, t)
assert esc and 0 < esc["cruiser"] < 100, esc

# Imposible: 1 caza ligero vs 3000 lanzamisiles -> None (se descarta el origen)
assert auto_military_escort({"light_fighter": 1}, {}, {"rocket_launcher": 3000}, t, t) is None

# Greedy multi-tipo: pocos cruceros + cazas deben poder combinarse
esc2 = auto_military_escort({"cruiser": 2, "light_fighter": 500}, {},
                            {"rocket_launcher": 50}, t, t)
assert esc2 is not None and esc2.get("cruiser", 0) <= 2, esc2

print("auto_fleet self-check OK:", esc, esc2)

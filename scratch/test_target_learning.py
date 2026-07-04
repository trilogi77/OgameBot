# Check mínimo del aprendizaje por objetivo (mejoras 1 y 3):
#   - avg_real_loot / blacklist_state (targets.py)
#   - combat_target_coords (stats.py)
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ogbot.targets import avg_real_loot, blacklist_state
from ogbot.stats import combat_target_coords

NOW = time.time()
DAY = 86400

# --- avg_real_loot ---
assert avg_real_loot(None) == 0.0
assert avg_real_loot({}) == 0.0
assert avg_real_loot({"raids": 4, "loot": 200_000.0}) == 50_000.0

# --- blacklist_state ---
poor = {"raids": 3, "loot": 30_000.0, "last": NOW - 1 * DAY}   # media 10k < 50k
rich = {"raids": 3, "loot": 600_000.0, "last": NOW - 1 * DAY}  # media 200k
few = {"raids": 2, "loot": 0.0, "last": NOW - 1 * DAY}         # pocos raids aún
old_poor = {"raids": 3, "loot": 30_000.0, "last": NOW - 8 * DAY}

assert blacklist_state(poor, 50_000, NOW, days=7.0) == "skip"
assert blacklist_state(rich, 50_000, NOW, days=7.0) == "ok"
assert blacklist_state(few, 50_000, NOW, days=7.0) == "ok"
assert blacklist_state(old_poor, 50_000, NOW, days=7.0) == "reset"   # cumplió la condena
assert blacklist_state(poor, 50_000, NOW, days=0.0) == "ok"          # desactivado
assert blacklist_state(None, 50_000, NOW, days=7.0) == "ok"

# --- combat_target_coords ---
assert combat_target_coords({"coordinates": "1:222:8"}, "") == "1:222:8"
assert combat_target_coords({}, "Informe de combate de Fulano [2:35:10] (V:3.567.000)") == "2:35:10"
# el atributo manda sobre el texto
assert combat_target_coords({"coordinates": "3:44:5"}, "algo [9:99:9]") == "3:44:5"
# atributo con basura -> respaldo por texto
assert combat_target_coords({"coordinates": "n/a"}, "ataque a [4:120:6] terminado") == "4:120:6"
# sin nada -> vacío
assert combat_target_coords({}, "sin coordenadas aquí") == ""

print("test_target_learning: OK")

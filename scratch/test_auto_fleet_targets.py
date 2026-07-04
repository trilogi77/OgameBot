"""Self-check de auto_fleet_targets (auto-gestión de flota)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.fleet import auto_fleet_targets
from ogbot.models import Planet, Coords
from ogbot.config import Config

cfg = Config()  # enable_farming=True, farm_recycle_debris=True por defecto


def planet(buildings):
    return Planet(id="1", name="P", coords=Coords(1, 1, 1), buildings=buildings)


# Planeta pelado (astillero 1, sin investigación): nada cumple prerrequisitos
bare = planet({"shipyard": 1, "metal_mine": 2})
assert auto_fleet_targets(bare, [bare], {}, cfg) == {}

# Imperio desarrollado: objetivos escalan con las minas y respetan prerrequisitos
dev = planet({"shipyard": 7, "metal_mine": 20, "crystal_mine": 17})
tech = {"combustion_drive": 6, "espionage_tech": 3, "armor_tech": 2,
        "impulse_drive": 4, "ion_tech": 2, "hyperspace_drive": 4}
t = auto_fleet_targets(dev, [dev], tech, cfg)
eco = 37
assert t["espionage_probe"] == 12
assert t["large_cargo"] == eco * 2
assert t["recycler"] == eco // 3
assert t["light_fighter"] == eco * 2 and t["cruiser"] == eco // 2 and t["battleship"] == eco // 4
assert "small_cargo" not in t  # el grande ya está desbloqueado
# Prioridad: sondas y cargueros antes que la escolta militar
keys = list(t)
assert keys.index("large_cargo") < keys.index("light_fighter")

# Sin ion_tech no hay cruceros; sin carguero grande, el pequeño hace de puente
t2 = auto_fleet_targets(dev, [dev], {"combustion_drive": 2, "espionage_tech": 3}, cfg)
assert "cruiser" not in t2 and "large_cargo" not in t2
assert t2["small_cargo"] == 15

# Más economía (segundo planeta) -> objetivos mayores
dev2 = planet({"shipyard": 7, "metal_mine": 15, "crystal_mine": 12})
t3 = auto_fleet_targets(dev, [dev, dev2], tech, cfg)
assert t3["large_cargo"] > t["large_cargo"]

print("auto_fleet_targets self-check OK:", t)

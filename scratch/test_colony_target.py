import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import moons
from ogbot.models import Coords

cfg = types.SimpleNamespace(preferred_colony_positions=[4, 5, 6, 7, 8, 9, 10, 11, 12])

# 1) Con zona objetivo libre, coloniza EN esa galaxia:sistema.
dest = moons.pick_colony(set(), cfg, home_coords=Coords(3, 250, 1))
assert dest.galaxy == 3 and dest.system == 250, dest

# 2) Si el sistema objetivo está lleno (todas las posiciones preferidas ocupadas),
#    se expande a sistemas adyacentes de la MISMA galaxia (no salta de galaxia aún).
occupied = {(3, 250, p) for p in cfg.preferred_colony_positions}
dest = moons.pick_colony(occupied, cfg, home_coords=Coords(3, 250, 1))
assert dest.galaxy == 3 and dest.system in (249, 251), dest

# 3) Sin zona objetivo (la lógica del brain pasa home_coords=casa): expande desde casa.
dest = moons.pick_colony(set(), cfg, home_coords=Coords(1, 5, 1))
assert dest.galaxy == 1 and dest.system == 5, dest

# 4) Validación de bounds que hace el brain: fuera de rango -> se ignora (usa casa).
def origin_for(tg, ts, home):
    if 1 <= tg <= 9 and 1 <= ts <= 499:
        return Coords(tg, ts, 1)
    return home
home = Coords(2, 113, 10)
assert origin_for(0, 0, home) is home            # sin poner -> casa
assert origin_for(4, 999, home) is home          # sistema fuera de rango -> casa
assert origin_for(4, 200, home) == Coords(4, 200, 1)  # válido -> objetivo

print("OK")

"""
moons.py + colonizer  (lógica pura)
===================================
moon_chance / plan_moonshot:
  La probabilidad de luna = min(0.20, escombros_totales / 100000).
  Para forzar una luna creamos un campo de escombros sobre NUESTRO planeta
  sacrificando naves (típicamente cazas ligeros) — atacándonos a nosotros mismos
  no se puede, así que el método habitual es:
    a) dejar que un atacante se estrelle contra tu defensa, o
    b) coordinar el "crasheo" de naves propias mediante un aliado, o
    c) en universos con ello, usar la mecánica disponible.
  Aquí calculamos cuántas naves equivalen al campo de escombros objetivo.

colony selection:
  Elige el mejor (galaxia, sistema, posición) libre dado el preferred_positions
  (las posiciones centrales 4-12 dan más campos y mejor temperatura para deut).
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple
from . import gamedata as gd
from .models import Coords
from .config import Config


def moon_chance(debris_total: float) -> float:
    return min(0.20, debris_total / 100_000.0)


def ships_for_debris(target_debris: int, ship: str, debris_factor: float) -> int:
    """Cuántas naves hay que destruir para alcanzar el campo de escombros objetivo."""
    u = gd.SHIPS[ship]
    value_per_ship = (u.cost.metal + u.cost.crystal) * debris_factor
    if value_per_ship <= 0:
        return 0
    return math.ceil(target_debris / value_per_ship)


def plan_moonshot(planet_coords: Coords, cfg: Config) -> dict:
    n = ships_for_debris(cfg.moon_target_debris, cfg.moon_sacrifice_ship, cfg.debris_factor)
    return {
        "where": planet_coords,
        "sacrifice_ship": cfg.moon_sacrifice_ship,
        "ships_to_crash": n,
        "expected_chance": moon_chance(cfg.moon_target_debris),
        "note": ("Crear lunas requiere un campo de escombros sobre el planeta. "
                 "No puedes atacarte a ti mismo: necesita un crash coordinado "
                 "(aliado/segunda cuenta NO permitida) o aprovechar un ataque "
                 "entrante contra tu defensa. Revisa reglas del universo."),
    }


# ----------------------------- COLONIZACIÓN -------------------------------
POSITION_FIELDS = {  # campos base aproximados por posición (planeta principal)
    1: 96, 2: 104, 3: 112, 4: 143, 5: 163, 6: 169, 7: 163,
    8: 149, 9: 130, 10: 114, 11: 110, 12: 99, 13: 90, 14: 90, 15: 90,
}
POSITION_TEMP = {  # temperatura máxima aprox. (más fría = más deuterio)
    4: 40, 5: 30, 6: 20, 7: 10, 8: 0, 9: -10, 10: -20, 11: -30, 12: -40,
}


def score_position(position: int) -> float:
    fields = POSITION_FIELDS.get(position, 90)
    temp = POSITION_TEMP.get(position, 30)
    # más campos = mejor; más frío = más deut (mejor para autonomía de flota)
    return fields * 1.0 + (30 - temp) * 0.5


def pick_colony(occupied: set, cfg: Config,
                home_coords: "Coords | None" = None,
                galaxy_range: Tuple[int, int] = (1, 9),
                system_range: Tuple[int, int] = (1, 499)) -> Optional[Coords]:
    """
    Busca el primer hueco libre priorizando:
    1. Misma galaxia que home_coords, expandiéndose desde el sistema de origen.
    2. Resto de galaxias en orden.
    """
    ordered_positions = sorted(cfg.preferred_colony_positions,
                               key=score_position, reverse=True)

    if home_coords is not None:
        hg, hs = home_coords.galaxy, home_coords.system
        for radius in range(0, 499):
            for s in ([hs] if radius == 0 else [hs + radius, hs - radius]):
                if not (1 <= s <= 499):
                    continue
                for p in ordered_positions:
                    if (hg, s, p) not in occupied:
                        return Coords(hg, s, p)
        other_galaxies = [g for g in range(galaxy_range[0], galaxy_range[1] + 1) if g != hg]
    else:
        other_galaxies = list(range(galaxy_range[0], galaxy_range[1] + 1))

    for g in other_galaxies:
        for s in range(system_range[0], system_range[1] + 1):
            for p in ordered_positions:
                if (g, s, p) not in occupied:
                    return Coords(g, s, p)
    return None

"""
startorder.py
=============
Órdenes de desarrollo fijos para las "configuraciones especiales" de la GUI.

 - SERVER_START_ORDER: arranque de un universo nuevo (un solo planeta). Sigue la
   meta minera clásica: metal ~2 niveles por encima de cristal, una placa solar
   cada ~2 niveles de mina, deuterio por detrás, robótica 2 antes del astillero,
   laboratorio y las tecnologías justas (energía, combustión, espionaje e
   impulso) hasta Astrofísica 1 para poder colonizar cuanto antes.
 - NEW_PLANET_ORDER: desarrollo de una colonia recién fundada. Solo edificios:
   la investigación es global y la lleva el planeta principal.

Cada paso es (tipo, nombre, nivel_objetivo) con niveles ACUMULATIVOS: el motor
construye nivel a nivel hasta alcanzar el objetivo antes de pasar al siguiente,
así que el mismo orden sirve para retomar un programa a medias.
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple

B, R = "building", "research"

SERVER_START_ORDER: List[Tuple[str, str, int]] = [
    (B, "metal_mine", 2),
    (B, "solar_plant", 1),
    (B, "metal_mine", 4),
    (B, "solar_plant", 2),
    (B, "crystal_mine", 2),
    (B, "solar_plant", 3),
    (B, "metal_mine", 5),
    (B, "crystal_mine", 3),
    (B, "solar_plant", 4),
    (B, "deut_synth", 2),
    (B, "metal_mine", 6),
    (B, "crystal_mine", 4),
    (B, "solar_plant", 5),
    (B, "deut_synth", 3),
    (B, "robotics_factory", 2),
    (B, "metal_mine", 7),
    (B, "solar_plant", 6),
    (B, "crystal_mine", 5),
    (B, "shipyard", 1),
    (B, "research_lab", 1),
    (R, "energy_tech", 1),
    (R, "combustion_drive", 2),
    (B, "metal_mine", 8),
    (B, "solar_plant", 7),
    (B, "crystal_mine", 6),
    (B, "deut_synth", 4),
    (B, "shipyard", 2),
    (B, "research_lab", 2),
    (R, "impulse_drive", 1),
    (B, "crystal_mine", 7),
    (B, "solar_plant", 8),
    (B, "research_lab", 3),
    (R, "espionage_tech", 2),
    (B, "metal_mine", 9),
    (B, "deut_synth", 5),
    (R, "espionage_tech", 4),
    (R, "impulse_drive", 3),
    (B, "shipyard", 4),
    (R, "astrophysics", 1),
    (B, "metal_mine", 10),
    (B, "solar_plant", 9),
    (B, "crystal_mine", 8),
    (B, "deut_synth", 6),
]

NEW_PLANET_ORDER: List[Tuple[str, str, int]] = [
    (B, "solar_plant", 2),
    (B, "metal_mine", 4),
    (B, "solar_plant", 3),
    (B, "crystal_mine", 2),
    (B, "metal_mine", 6),
    (B, "solar_plant", 5),
    (B, "crystal_mine", 4),
    (B, "deut_synth", 2),
    (B, "robotics_factory", 2),
    (B, "metal_mine", 8),
    (B, "solar_plant", 7),
    (B, "crystal_mine", 6),
    (B, "deut_synth", 4),
    (B, "shipyard", 1),
    (B, "metal_mine", 10),
    (B, "solar_plant", 9),
    (B, "crystal_mine", 8),
    (B, "deut_synth", 6),
    (B, "metal_mine", 12),
    (B, "solar_plant", 11),
    (B, "crystal_mine", 10),
    (B, "deut_synth", 8),
]


def next_step(planet, research_levels: Optional[Dict[str, int]],
              order: List[Tuple[str, str, int]]) -> Optional[Tuple[str, str, int]]:
    """Primer paso del orden aún no alcanzado, o None si el programa está completo."""
    for kind, name, lvl in order:
        cur = planet.lvl(name) if kind == B else int((research_levels or {}).get(name, 0))
        if cur < lvl:
            return kind, name, lvl
    return None


def storage_capacity(level: int) -> int:
    return 5000 * math.floor(2.5 * math.exp(20 * level / 33))


def storage_blocker(cost, planet) -> Optional[str]:
    """Almacén a subir ANTES del paso si su coste no cabe en la capacidad actual
    (si no, el objetivo sería inalcanzable por mucho que se acumule)."""
    for res, st in (("metal", "metal_storage"), ("crystal", "crystal_storage"),
                    ("deut", "deut_tank")):
        if getattr(cost, res, 0) > storage_capacity(planet.lvl(st)):
            return st
    return None

from __future__ import annotations
from typing import Dict, Tuple, Optional
from . import gamedata as gd
from .models import Planet

def resolve_prerequisites(
    target_type: str,
    target_name: str,
    target_level: int,
    planet: Planet,
    research_levels: Dict[str, int],
    visited = None
) -> Optional[Tuple[str, str, int]]:
    """
    Resuelve recursivamente los prerrequisitos para un objetivo (edificio, investigación o nave).
    Retorna la acción inmediata necesaria (type, name, level) o None si hay una referencia circular.
    Si ya se cumplen todos los requisitos, retorna el objetivo mismo: (target_type, target_name, target_level).
    """
    if visited is None:
        visited = set()

    key = (target_type, target_name, target_level)
    if key in visited:
        # Evitar bucles infinitos
        return None
    visited.add(key)

    if target_type == "building":
        # 1. Comprobar si ya tenemos este nivel
        if planet.lvl(target_name) >= target_level:
            return target_type, target_name, target_level

        # 2. Comprobar prerrequisitos de este edificio
        prereqs = gd.BUILDING_PREREQS.get(target_name, {})
        for req_name, req_lvl in prereqs.items():
            if req_name in gd.RESEARCH_COST:
                current_lvl = research_levels.get(req_name, 0)
                if current_lvl < req_lvl:
                    sub = resolve_prerequisites("research", req_name, current_lvl + 1, planet, research_levels, visited)
                    if sub:
                        return sub
            elif req_name in gd.BUILDING_COST:
                current_lvl = planet.lvl(req_name)
                if current_lvl < req_lvl:
                    sub = resolve_prerequisites("building", req_name, current_lvl + 1, planet, research_levels, visited)
                    if sub:
                        return sub

        return target_type, target_name, target_level

    elif target_type == "research":
        # 1. Comprobar si ya tenemos esta investigación
        current_research_lvl = research_levels.get(target_name, 0)
        if current_research_lvl >= target_level:
            return target_type, target_name, target_level

        # 2. Comprobar requisito de laboratorio de investigación en el planeta
        lab_req = gd.RESEARCH_LAB_REQ.get(target_name, 0)
        if planet.lvl("research_lab") < lab_req:
            sub = resolve_prerequisites("building", "research_lab", planet.lvl("research_lab") + 1, planet, research_levels, visited)
            if sub:
                return sub

        # 3. Comprobar otros requisitos de investigación
        prereqs = gd.RESEARCH_PREREQS.get(target_name, {})
        for req_name, req_lvl in prereqs.items():
            current_lvl = research_levels.get(req_name, 0)
            if current_lvl < req_lvl:
                sub = resolve_prerequisites("research", req_name, current_lvl + 1, planet, research_levels, visited)
                if sub:
                    return sub

        return target_type, target_name, target_level

    elif target_type == "ship":
        # 1. Comprobar prerrequisitos de este barco
        prereqs = gd.SHIP_PREREQS.get(target_name, {})
        for req_name, req_lvl in prereqs.items():
            if req_name in gd.RESEARCH_COST:
                current_lvl = research_levels.get(req_name, 0)
                if current_lvl < req_lvl:
                    sub = resolve_prerequisites("research", req_name, current_lvl + 1, planet, research_levels, visited)
                    if sub:
                        return sub
            elif req_name in gd.BUILDING_COST:
                current_lvl = planet.lvl(req_name)
                if current_lvl < req_lvl:
                    sub = resolve_prerequisites("building", req_name, current_lvl + 1, planet, research_levels, visited)
                    if sub:
                        return sub

        return target_type, target_name, 1

    return None
